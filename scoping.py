"""会话作用域：把当前聊天归入“全局 / 某个群 / 某个人”三类目录之一。

作用域只决定文件放哪个子目录，不改变权限；派生规则：
- 群聊消息（能拿到群号）→ ``群聊-<群号>``
- 网页测试台（webchat）→ ``网页-<用户名>``
- 其他私聊 → ``私聊-<对方QQ>``
"""

from __future__ import annotations

import re

_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPLIT = re.compile(r"\s+")


def _clean(value: str, limit: int = 60) -> str:
    name = _BAD_NAME.sub("_", str(value or "")).strip().strip(". ")
    name = _SPLIT.sub("-", name)
    return name[:limit]


def derive_scope_id(
    *,
    platform_id: str = "",
    umo: str = "",
    group_id: str = "",
    sender_id: str = "",
) -> str:
    """从事件要素派生作用域 ID；无法判定时返回空串（退回全局）。"""
    group = _clean(group_id)
    if group:
        return f"群聊-{group}"
    if str(platform_id or "").lower() == "webchat":
        tail = str(umo or "").rsplit(":", 1)[-1]
        parts = tail.split("!")
        user = _clean(parts[1]) if len(parts) > 1 else ""
        if user:
            return f"网页-{user}"
        return "网页"
    sender = _clean(sender_id)
    if sender:
        return f"私聊-{sender}"
    tail = _clean(str(umo or "").rsplit(":", 1)[-1])
    if tail:
        return f"私聊-{tail}"
    return ""


def scope_subdir(scope_id: str) -> str:
    """作用域 ID → 安全的子目录名；空 ID 返回空串（用全局目录）。"""
    name = _clean(scope_id)
    return name


def scope_kind(scope_id: str) -> str:
    """作用域的大类：群聊 / 私聊 / 网页 / 全局，用于日记提示语。"""
    text = str(scope_id or "")
    if text.startswith("群聊-"):
        return "群聊"
    if text.startswith("私聊-"):
        return "私聊"
    if text.startswith("网页-"):
        return "网页"
    return "全局"
