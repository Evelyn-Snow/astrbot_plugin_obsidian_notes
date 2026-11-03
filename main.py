"""机器人的本机 Obsidian 笔记/日记工具。

Obsidian 仓库只是 Markdown 目录，因此不需要 Obsidian 常驻，也不依赖 REST 插件。
LivingMemory 负责语义记忆；这里负责人类可读、可手改的日记与长笔记。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.config.astrbot_config import AstrBotConfig

from .recording import append_diary_entry, month_of
from .scoping import derive_scope_id, scope_kind, scope_subdir

NAME = "astrbot_plugin_obsidian_notes"
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ObsidianNotes(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.data_dir = StarTools.get_data_dir(NAME)
        self._auto_buffer: list[dict] = []
        self._auto_task: asyncio.Task | None = None
        self._auto_last_attempt = 0.0

    # ---------------- 基础 ----------------
    @staticmethod
    def _clean_name(value: str, fallback: str = "未命名") -> str:
        name = _BAD_NAME.sub("_", str(value or "")).strip().strip(". ")
        return (name[:80] or fallback)

    def _vault(self) -> Path:
        raw = str(self.conf.get("vault_path") or "~/Documents/BotMemory").strip()
        vault = Path(os.path.expandvars(raw)).expanduser()
        vault.mkdir(parents=True, exist_ok=True)
        return vault.resolve()

    def _folder(self, key: str, default: str) -> Path:
        name = self._clean_name(self.conf.get(key) or default, default)
        path = (self._vault() / name).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _diary_dir(self) -> Path:
        return self._folder("diary_folder", "日记")

    def _notes_dir(self) -> Path:
        return self._folder("notes_folder", "笔记")

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        owner = str(self.conf.get("owner_session") or "").strip()
        return bool((owner and event.unified_msg_origin == owner) or event.is_admin())

    @staticmethod
    def _deny() -> str:
        return "只有所有者能让机器人写或看笔记。用你的口吻轻轻拒绝，不要提工具或路径。"

    def _scope_id(self, event: AstrMessageEvent) -> str:
        """当前会话的作用域 ID；作用域模式关闭时仍可派生，由调用方决定是否使用。"""
        group_getter = getattr(event, "get_group_id", None)
        group_id = ""
        if callable(group_getter):
            try:
                group_id = str(group_getter() or "")
            except Exception:
                group_id = ""
        return derive_scope_id(
            platform_id=event.get_platform_name(),
            umo=str(getattr(event, "unified_msg_origin", "") or ""),
            group_id=group_id,
            sender_id=str(getattr(event, "get_sender_id", lambda: "")() or ""),
        )

    @staticmethod
    def _wants_session_scope(scope: str, mode: str) -> bool:
        """scope 参数：here=跟随当前会话（会话模式开启时生效），global=全局。"""
        value = str(scope or "here").strip().lower()
        if value in ("global", "全局", "shared"):
            return False
        return str(mode or "flat").strip().lower() == "session"

    def _note_path(self, title: str, folder: str = "", scope_id: str = "") -> Path:
        base = self._folder("notes_folder", "笔记")
        if scope_id:
            base = base / scope_subdir(scope_id)
            base.mkdir(parents=True, exist_ok=True)
        if folder.strip():
            base = base / self._clean_name(folder)
            base.mkdir(parents=True, exist_ok=True)
        name = self._clean_name(title)
        if not name.lower().endswith(".md"):
            name += ".md"
        path = (base / name).resolve()
        if self._vault() not in path.parents:
            raise ValueError("笔记路径越界")
        return path

    # ---------------- 工具 ----------------
    @filter.llm_tool(name="obsidian_write_note")
    async def write_note(
        self,
        event: AstrMessageEvent,
        title: str,
        content: str,
        folder: str = "",
        mode: str = "append",
        scope: str = "here",
    ) -> str:
        """把内容写进所有者的 Obsidian 笔记库。所有者明确让你记笔记、整理资料、保存一段内容时使用。
        默认追加到已有笔记；用户要求“新建/覆盖”时才用 replace。不要主动记录闲聊。

        Args:
            title(string): 笔记标题，例如「所有者喜欢的饮料」
            content(string): 要写入的 Markdown 内容
            folder(string): 笔记子目录，留空用「笔记」
            mode(string): append 追加（默认）或 replace 覆盖
            scope(string): here=只给当前会话/这个人看（默认）；global=所有会话共享。和当前对象有关的事用 here，共同兴趣或通用资料用 global
        """
        if not self._is_owner(event):
            return self._deny()
        if not str(content or "").strip():
            return "内容为空，没有写。"
        try:
            scope_id = ""
            if self._wants_session_scope(scope, self.conf.get("note_scope_mode")):
                scope_id = self._scope_id(event)
            path = self._note_path(title, folder, scope_id=scope_id)
            mode = "replace" if str(mode).lower() == "replace" else "append"
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            body = str(content).strip()
            if path.exists() and mode == "append":
                old = path.read_text(encoding="utf-8-sig").rstrip()
                text = old + "\n\n" + body
            else:
                text = (
                    "---\n"
                    f"created: {now}\n"
                    f"updated: {now}\n"
                    "tags: [笔记]\n"
                    "---\n\n"
                    f"# {path.stem}\n\n"
                    f"{body}"
                )
            path.write_text(text.rstrip() + "\n", encoding="utf-8")
            logger.info("[obsidian_notes] wrote %s", path)
            return f"已写入 Obsidian 笔记「{path.stem}」。"
        except Exception as exc:
            logger.error("[obsidian_notes] write failed: %s", exc)
            return "笔记没写成，文件系统报错了。用你的口吻说一句等会儿再试，别提技术细节。"

    @filter.llm_tool(name="obsidian_append_diary")
    async def append_diary(
        self,
        event: AstrMessageEvent,
        content: str,
        day: str = "",
        scope: str = "here",
    ) -> str:
        """把一件事写进所有者的 Obsidian 日记，默认写到今天。所有者说“记日记/写进日记/记下今天”时使用。

        Args:
            content(string): 日记正文，简短自然
            day(string): 可选日期 YYYY-MM-DD，留空表示今天
            scope(string): here=当前会话的日记（默认）；global=全局日记。和当前对象之间的事用 here，跨会话的通用记录用 global
        """
        if not self._is_owner(event):
            return self._deny()
        body = str(content or "").strip()
        if not body:
            return "日记内容为空，没有写。"
        try:
            scope_id = ""
            if self._wants_session_scope(scope, self.conf.get("diary_scope_mode")):
                scope_id = self._scope_id(event)
            path = self._append_diary_text(day, body, scope_id=scope_id)
            return f"已写进 {path.stem} 的日记。"
        except Exception as exc:
            logger.error("[obsidian_notes] diary failed: %s", exc)
            return "日记没写成，文件系统报错了。用你的口吻说一句等会儿再试，别提技术细节。"

    def _append_diary_text(self, day: str, body: str, scope_id: str = "") -> Path:
        day = str(day or "").strip()
        if day:
            datetime.strptime(day, "%Y-%m-%d")
        else:
            day = date.today().isoformat()
        base = self._diary_dir()
        if scope_id:
            base = base / scope_subdir(scope_id)
            base.mkdir(parents=True, exist_ok=True)
        month = month_of(day)
        path = base / f"{month}.md"
        stamp = time.strftime("%H:%M")
        entry = f"- {stamp} {str(body).strip().replace(chr(10), chr(10) + '  ')}"
        if path.exists():
            text = path.read_text(encoding="utf-8-sig")
        else:
            text = f"# {month}\n"
        text = append_diary_entry(text, day, entry)
        path.write_text(text, encoding="utf-8")
        logger.info("[obsidian_notes] diary %s", path)
        return path

    @filter.llm_tool(name="obsidian_read_note")
    async def read_note(self, event: AstrMessageEvent, title: str) -> str:
        """读取所有者 Obsidian 笔记库中的一篇笔记。回答过去的事、所有者偏好、约定前，若记得有相关笔记可先读。
        优先找当前会话专属的笔记，找不到会自动找全局的。

        Args:
            title(string): 笔记标题，可带或不带 .md
        """
        if not self._is_owner(event):
            return self._deny()
        try:
            scope_id = ""
            if self._wants_session_scope("here", self.conf.get("note_scope_mode")):
                scope_id = self._scope_id(event)
            path = self._note_path(title, scope_id=scope_id)
            if not path.exists():
                matches = list(self._notes_dir().rglob(self._clean_name(title) + ".md"))
                if not matches:
                    return f"没有找到笔记「{self._clean_name(title)}」。"
                path = matches[0]
            text = path.read_text(encoding="utf-8-sig")
            limit = max(1000, int(self.conf.get("max_read_chars") or 6000))
            if len(text) > limit:
                text = text[:limit] + "\n…（后面省略）"
            return f"笔记「{path.stem}」：\n{text}"
        except Exception as exc:
            logger.error("[obsidian_notes] read failed: %s", exc)
            return "笔记没读出来，文件系统报错了。用你的口吻说一句等会儿再试，别提技术细节。"

    @filter.llm_tool(name="obsidian_search_notes")
    async def search_notes(
        self,
        event: AstrMessageEvent,
        query: str,
        limit: int = 5,
    ) -> str:
        """在所有者的 Obsidian 笔记和日记里按关键词搜索，用于回忆过去的事。找不到时不要编造。

        Args:
            query(string): 搜索关键词，例如「生日」「喜欢的饮料」
            limit(number): 最多返回几条，默认 5
        """
        if not self._is_owner(event):
            return self._deny()
        terms = [t for t in re.split(r"\s+", str(query or "").strip()) if t]
        if not terms:
            return "搜索词为空。"
        max_results = max(1, min(int(limit or 5), 10))
        try:
            scored = []
            for path in self._vault().rglob("*.md"):
                if any(part.startswith(".") for part in path.parts):
                    continue
                try:
                    text = path.read_text(encoding="utf-8-sig")
                except Exception:
                    continue
                lowered = text.lower()
                score = sum(lowered.count(term.lower()) for term in terms)
                if score <= 0:
                    continue
                hits = [lowered.find(term.lower()) for term in terms if term.lower() in lowered]
                pos = min(hits, default=0)
                snippet = text[max(0, pos - 80): pos + 220].replace("\n", " ")
                scored.append((score, path, snippet))
            scored.sort(key=lambda item: item[0], reverse=True)
            if not scored:
                return "笔记里没有找到相关内容。"
            lines = [f"在 Obsidian 里找到 {len(scored[:max_results])} 条相关内容："]
            for score, path, snippet in scored[:max_results]:
                rel = path.relative_to(self._vault()).as_posix()
                lines.append(f"- {rel}：{snippet.strip()}")
            return "\n".join(lines)
        except Exception as exc:
            logger.error("[obsidian_notes] search failed: %s", exc)
            return "笔记搜索失败，文件系统报错了。用你的口吻说一句等会儿再试，别提技术细节。"

    @filter.llm_tool(name="obsidian_list_notes")
    async def list_notes(self, event: AstrMessageEvent, limit: int = 30) -> str:
        """列出所有者 Obsidian 笔记库里的 Markdown 文件，用于了解有哪些笔记可查。

        Args:
            limit(number): 最多列出几条，默认 30
        """
        if not self._is_owner(event):
            return self._deny()
        max_results = max(1, min(int(limit or 30), 100))
        try:
            files = [
                p for p in self._vault().rglob("*.md")
                if not any(part.startswith(".") for part in p.parts)
            ]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            if not files:
                return "笔记库还是空的。"
            lines = [f"Obsidian 笔记库共 {len(files)} 篇，最近 {min(len(files), max_results)} 篇："]
            for path in files[:max_results]:
                lines.append("- " + path.relative_to(self._vault()).as_posix())
            return "\n".join(lines)
        except Exception as exc:
            logger.error("[obsidian_notes] list failed: %s", exc)
            return "读取笔记目录失败。"

    # ---------------- 自动日记 ----------------
    def _owner_session(self) -> str:
        return str(self.conf.get("owner_session") or "").strip()

    def _owner_qq(self) -> str:
        owner = self._owner_session()
        tail = owner.rsplit(":", 1)[-1] if owner else ""
        return tail if tail.isdigit() else ""

    def _session_scope(self, event: AstrMessageEvent) -> tuple[str, int]:
        """返回 (范围, 权重)。权重只影响摘要优先度。"""
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        owner = self._owner_session()
        owner_qq = self._owner_qq()
        sender = str(getattr(event, "get_sender_id", lambda: "")() or "")
        group_getter = getattr(event, "get_group_id", None)
        group_id = ""
        if callable(group_getter):
            try:
                group_id = str(group_getter() or "")
            except Exception:
                group_id = ""
        is_group = bool(group_id) or "GroupMessage" in origin
        owner_weight = max(1, int(self.conf.get("auto_diary_owner_weight") or 3))
        group_weight = max(1, int(self.conf.get("auto_diary_group_weight") or 2))
        other_weight = max(1, int(self.conf.get("auto_diary_other_weight") or 1))
        if owner and origin == owner:
            return "所有者私聊", owner_weight
        if owner_qq and sender == owner_qq:
            return "所有者参与的群聊" if is_group else "所有者私聊", max(owner_weight, group_weight)
        if is_group:
            return "群聊", group_weight
        return "其他私聊", other_weight

    def _select_auto_context(self, snapshot: list[dict]) -> list[dict]:
        owner_items = [
            item for item in snapshot
            if str(item.get("scope")) in ("所有者私聊", "所有者参与的群聊")
        ]
        other_items = [
            item for item in snapshot
            if str(item.get("scope")) not in ("所有者私聊", "所有者参与的群聊")
        ]
        selected_owner = owner_items[-8:]
        selected_other = sorted(
            other_items,
            key=lambda item: (int(item.get("weight") or 1), float(item.get("time") or 0)),
            reverse=True,
        )[:4]
        if not selected_owner:
            selected_other = other_items[-12:]
        selected = selected_owner + selected_other
        selected.sort(key=lambda item: float(item.get("time") or 0))
        return selected

    def _state_path(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "auto_diary_state.json"

    def _load_state(self) -> dict:
        try:
            data = json.loads(self._state_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            self._state_path().write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("[obsidian_notes] save diary state failed: %s", exc)

    @staticmethod
    def _in_quiet_hours(spec: str, hour: int) -> bool:
        spec = str(spec or "").strip()
        if not spec:
            return False
        for part in re.split(r"[,，\s]+", spec):
            if not part:
                continue
            try:
                if "-" in part:
                    start_s, end_s = part.split("-", 1)
                    start, end = int(start_s), int(end_s)
                else:
                    start = end = int(part)
            except ValueError:
                continue
            start, end = max(0, start) % 24, max(0, end) % 24
            if start == end:
                continue
            if start < end:
                if start <= hour < end:
                    return True
            elif hour >= start or hour < end:
                return True
        return False

    def _auto_due(self) -> bool:
        if not bool(self.conf.get("auto_diary_enabled", True)):
            return False
        owner = self._owner_session()
        if not owner or not self._auto_buffer:
            return False
        now = time.time()
        if self._in_quiet_hours(
            str(self.conf.get("auto_diary_quiet_hours") or ""),
            datetime.now().hour,
        ):
            return False
        min_exchanges = max(1, int(self.conf.get("auto_diary_min_exchanges") or 3))
        if len(self._auto_buffer) < min_exchanges:
            return False
        if now - self._auto_last_attempt < 300:
            return False
        state = self._load_state()
        today = date.today().isoformat()
        if state.get("date") != today:
            state = {"date": today, "count": 0, "last_ts": float(state.get("last_ts") or 0)}
        max_per_day = max(1, int(self.conf.get("auto_diary_max_per_day") or 3))
        if int(state.get("count") or 0) >= max_per_day:
            return False
        interval = max(0, int(self.conf.get("auto_diary_min_interval_minutes") or 120))
        if now - float(state.get("last_ts") or 0) < interval * 60:
            return False
        return True

    @filter.on_llm_response(priority=100000)
    async def capture_for_auto_diary(self, event: AstrMessageEvent, response):
        if not bool(self.conf.get("auto_diary_enabled", True)):
            return
        if not self._owner_session():
            return
        scope, weight = self._session_scope(event)
        if scope == "群聊" and not bool(self.conf.get("auto_diary_include_groups", True)):
            return
        if scope == "其他私聊" and not bool(
            self.conf.get("auto_diary_include_other_private", True)
        ):
            return
        user = str(getattr(event, "message_str", "") or "").strip()
        reply = str(getattr(response, "completion_text", "") or "").strip()
        if not user or not reply or user.startswith(("/", "!")):
            return
        self._auto_buffer.append(
            {
                "time": time.time(),
                "scope": scope,
                "scope_id": self._scope_id(event),
                "weight": weight,
                "user": user[:500],
                "assistant": reply[:800],
            }
        )
        self._auto_buffer = self._auto_buffer[-40:]
        if self._auto_due() and (self._auto_task is None or self._auto_task.done()):
            self._auto_task = asyncio.create_task(self._write_auto_diary())

    @staticmethod
    def _clean_auto_text(text: str, limit: int) -> str:
        value = re.sub(r"\s+", " ", str(text or "")).strip()
        value = value.strip("\"'“”‘’ ")
        value = re.sub(r"^(?:日记|日记)\s*[:：]\s*", "", value)
        if len(value) > limit:
            value = value[: max(1, limit - 1)].rstrip("，。！？；：,.!?;: ") + "…"
        return value

    def _virtual_context_block(self) -> str:
        """虚拟人生日程 + 真实经历插件联动（检测分支，任一缺失/失败都静默跳过）。"""
        sections: list[str] = []
        try:
            if self.context.get_registered_star("astrbot_plugin_virtual_life"):
                path = Path(StarTools.get_data_dir("astrbot_plugin_virtual_life")) / "plans.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                plans = data.get("plans") if isinstance(data, dict) else None
                today = date.today().isoformat()
                hits = [
                    v
                    for k, v in (plans or {}).items()
                    if isinstance(v, dict) and str(k).startswith(f"{today}::")
                ]
                plan = hits[0] if len(hits) == 1 else None
                if plan:
                    timeline = "；".join(
                        f"{item.get('start')} {item.get('activity')}"
                        for item in (plan.get("timeline") or [])[:14]
                        if isinstance(item, dict)
                    )[:400]
                    sections.append(
                        f"【今日虚拟日程】主题：{plan.get('theme') or '日常'}，"
                        f"心情：{plan.get('mood') or '平静'}。安排：{timeline}"
                    )
        except Exception as exc:
            logger.debug(f"[{NAME}] 读取虚拟日程失败：{exc}")
        try:
            if self.context.get_registered_star("astrbot_plugin_snow_experience"):
                # 经历目录跟随 snow_experience 的 events_dir 配置（读不到配置时回退其数据目录）
                base = None
                meta = self.context.get_registered_star("astrbot_plugin_snow_experience")
                plugin = getattr(meta, "star_cls", None) if meta else None
                configured = str(getattr(plugin, "conf", {}).get("events_dir") or "").strip() if plugin else ""
                if configured:
                    base = Path(os.path.expandvars(configured))
                if base is None:
                    base = Path(StarTools.get_data_dir("astrbot_plugin_snow_experience")) / "events"
                events_dir = base
                files = sorted(events_dir.glob("*.md"), key=lambda p: p.name)[-2:]
                for file in files:
                    text = file.read_text(encoding="utf-8")
                    body_lines = [
                        line.strip()
                        for line in text.splitlines()
                        if line.strip() and not line.startswith(("# ", "- "))
                    ]
                    if body_lines:
                        sections.append(f"【最近真实经历】{file.stem}：{'；'.join(body_lines)[:200]}")
        except Exception as exc:
            logger.debug(f"[{NAME}] 读取真实经历失败：{exc}")
        return "\n".join(sections)

    async def _write_auto_diary(self):
        self._auto_last_attempt = time.time()
        owner = self._owner_session()
        buffered_count = len(self._auto_buffer)
        snapshot = self._select_auto_context(list(self._auto_buffer[-40:]))
        if not owner or not snapshot:
            return
        lines = []
        total = 0
        for item in snapshot:
            line = (
                f"[{item.get('scope', '对话')}] "
                f"对方：{item.get('user', '')}\n"
                f"机器人：{item.get('assistant', '')}"
            )
            if total + len(line) > 3200:
                break
            lines.append(line)
            total += len(line)
        limit = max(40, min(int(self.conf.get("auto_diary_max_chars") or 120), 300))
        source = "\n\n".join(lines)
        extra = self._virtual_context_block()
        if extra:
            source = f"{source}\n\n{extra}"
        prompt = (
            "根据下面最近的对话，写一条今天的日记。这些对话包含所有者私聊、群聊和其他人私聊，"
            "已按重要性筛选。请优先记录所有者私聊或所有者参与的群聊中值得记住的事、所有者的偏好、"
            "情绪或约定；群聊和其他人私聊只保留真正有信息量、与所有者相关的部分，不写无关闲聊。"
            "要求：第一人称；1~2 句；不超过 "
            f"{limit} 字；不要逐句复述，不要编造，不要写 QQ 号、昵称、群号、文件路径、工具名或系统提示；"
            "直接输出日记正文，不要标题、引号、列表。\n\n"
            f"对话：\n{source}"
        )
        try:
            provider = await self.context.get_using_provider_async(umo=owner)
            if provider is None:
                return
            result = await provider.text_chat(
                prompt=prompt,
                system_prompt="你是这个聊天机器人。写日记时用第一人称、口语、短句，只写真实发生的事。",
            )
            text = self._clean_auto_text(getattr(result, "completion_text", ""), limit)
            if not text:
                return
            self._append_diary_text("", text)
            state = self._load_state()
            today = date.today().isoformat()
            if state.get("date") != today:
                state = {"date": today, "count": 0, "last_ts": 0}
            state["count"] = int(state.get("count") or 0) + 1
            state["last_ts"] = time.time()
            self._save_state(state)
            logger.info(
                "[obsidian_notes] auto diary written (%s chars, %s/%s today)",
                len(text),
                state["count"],
                self.conf.get("auto_diary_max_per_day") or 3,
            )
            if bool(self.conf.get("auto_diary_per_scope", False)):
                await self._write_per_scope_diaries(
                    list(self._auto_buffer[-40:]), owner, limit
                )
            # 保留自动总结期间新到达的消息，避免它们被一起清掉。
            self._auto_buffer = self._auto_buffer[buffered_count:]
        except Exception as exc:
            logger.warning("[obsidian_notes] auto diary failed: %s", exc)
        finally:
            self._auto_task = None

    async def _write_per_scope_diaries(
        self, recent: list[dict], owner: str, limit: int
    ) -> None:
        """把最近对话按会话拆分，各自写一篇会话日记（最多 3 个会话/次）。"""
        by_scope: dict[str, list[dict]] = {}
        for item in recent:
            scope_id = str(item.get("scope_id") or "")
            if scope_id:
                by_scope.setdefault(scope_id, []).append(item)
        min_exchanges = max(1, int(self.conf.get("auto_diary_min_exchanges") or 3))
        max_per_day = max(1, int(self.conf.get("auto_diary_max_per_day") or 3))
        interval = max(0, int(self.conf.get("auto_diary_min_interval_minutes") or 120))
        today = date.today().isoformat()
        now = time.time()
        state = self._load_state()
        scopes_state = state.setdefault("scopes", {})
        try:
            provider = await self.context.get_using_provider_async(umo=owner)
            if provider is None:
                return
            written = 0
            for scope_id, items in by_scope.items():
                if written >= 3:
                    break
                if len(items) < min_exchanges:
                    continue
                s_state = scopes_state.get(scope_id) or {}
                if s_state.get("date") != today:
                    s_state = {"date": today, "count": 0, "last_ts": 0}
                if int(s_state.get("count") or 0) >= max_per_day:
                    continue
                if interval and now - float(s_state.get("last_ts") or 0) < interval * 60:
                    continue
                lines = []
                total = 0
                for item in items[-12:]:
                    line = (
                        f"对方：{item.get('user', '')}\n"
                        f"机器人：{item.get('assistant', '')}"
                    )
                    if total + len(line) > 3200:
                        break
                    lines.append(line)
                    total += len(line)
                if not lines:
                    continue
                kind = scope_kind(scope_id)
                prompt = (
                    f"根据下面最近的{kind}对话，写一条今天的日记，只记录这个会话里"
                    "值得记住的事、对方的偏好、情绪或约定。要求：第一人称；1~2 句；"
                    f"不超过 {limit} 字；不要逐句复述，不要编造，不要写 QQ 号、昵称、"
                    "群号、文件路径、工具名或系统提示；直接输出日记正文，"
                    "不要标题、引号、列表。\n\n"
                    f"对话：\n" + "\n\n".join(lines)
                )
                result = await provider.text_chat(
                    prompt=prompt,
                    system_prompt="你是这个聊天机器人。写日记时用第一人称、口语、短句，只写真实发生的事。",
                )
                text = self._clean_auto_text(getattr(result, "completion_text", ""), limit)
                if not text:
                    continue
                self._append_diary_text(today, text, scope_id=scope_id)
                s_state["count"] = int(s_state.get("count") or 0) + 1
                s_state["last_ts"] = now
                scopes_state[scope_id] = s_state
                written += 1
                logger.info("[obsidian_notes] per-scope diary written: %s", scope_id)
            if written:
                self._save_state(state)
        except Exception as exc:
            logger.warning("[obsidian_notes] per-scope auto diary failed: %s", exc)
