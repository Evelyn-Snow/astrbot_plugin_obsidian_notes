"""日记/经历的按月归档纯逻辑（不依赖 AstrBot，可单测）。"""

from __future__ import annotations

import re

_DAY_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")


def month_of(day: str) -> str:
    """YYYY-MM-DD → YYYY-MM；格式不对时原样返回前 7 位，调用方负责校验。"""
    return str(day or "").strip()[:7]


def append_diary_entry(text: str, day: str, entry: str) -> str:
    """把一条日记插入 ``## YYYY-MM-DD`` 小节；没有该小节就在末尾新建。

    同一天可能分多次写，也可能补写过去的日期，所以不能简单追加到文件末尾。
    """
    day = str(day or "").strip()
    heading = f"## {day}"
    entry = str(entry or "").rstrip()
    lines = str(text or "").rstrip().splitlines()
    index = next((i for i, line in enumerate(lines) if line.strip() == heading), None)
    if index is None:
        # 补写过去的日期时按日期顺序插入，别把 11-01 放到 11-02 后面
        insert_at = len(lines)
        for i, line in enumerate(lines):
            match = _DAY_HEADING.match(line.strip())
            if match and match.group(1) > day:
                insert_at = i
                break
        block = [heading, "", entry]
        if insert_at < len(lines):
            lines[insert_at:insert_at] = block + [""]
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines += block
    else:
        end = next(
            (i for i in range(index + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        insert_at = end
        while insert_at > index + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, entry)
    return "\n".join(lines).rstrip() + "\n"


def merge_daily_diary(files: list[tuple[str, str]]) -> str:
    """把 [(day, content)] 合并成一个月度日记文本（迁移用）。

    content 是旧版按天文件的内容，可能带 ``# YYYY-MM-DD`` 标题和空行。
    """
    items = sorted(files, key=lambda pair: pair[0])
    text = f"# {month_of(items[0][0])}\n" if items else ""
    for day, content in items:
        body = str(content or "").strip()
        if body.startswith("# "):
            body = body.split("\n", 1)[1].strip() if "\n" in body else ""
        text = append_diary_entry(text, day, body) if body else text
    return text.rstrip() + "\n"
