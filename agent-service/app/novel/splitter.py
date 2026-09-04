"""小说分章 / 分段工具（纯规则，无 LLM）。

- split_chapters：按 "第X章" 类中文标题切章；识别不到任何章节标记时把整段视作 1 章。
- split_paragraphs：先把长文本切成单句，再按 max_len 合并成段。
"""
from __future__ import annotations

import re
from typing import Any


# 匹配中文章节标题："第一章"、"第12章"、"第一章 标题"、"第一卷 第1章"等。
# 要求标记出现在行首（允许前置空白），后面可跟中文标题或标点。
_CHAPTER_RE = re.compile(
    r"^[ \t]*第[一二三四五六七八九十百千0-9]+[章节回卷]([：: 　]|$|\s)",
    re.MULTILINE,
)


def split_chapters(text: str) -> list[dict[str, Any]]:
    """把长文本切成 [{index: int, title: str, content: str}]。

    - index 从 1 开始，用于下游给 segment 编号 chapter 字段。
    - 找不到任何章节标记时返回单章（index=1，content=整段）。
    """
    if not text or not text.strip():
        return []

    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        # 兜底：整段当 1 章
        return [{"index": 1, "title": "正文", "content": text.strip()}]

    chapters: list[dict[str, Any]] = []
    # 匹配点之前如果有正文，也当作第 1 章（"引子"）
    if matches[0].start() > 0:
        head = text[: matches[0].start()].strip()
        if head:
            chapters.append({"index": 1, "title": "序章", "content": head})

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end]
        # 标题 = 第一个换行前的完整行
        first_newline = body.find("\n")
        if first_newline == -1:
            title = body.strip()
            content = ""
        else:
            title = body[:first_newline].strip()
            content = body[first_newline + 1:].strip()
        chapters.append({
            "index": len(chapters) + 1,
            "title": title,
            "content": content,
        })
    return chapters


def split_paragraphs(text: str, max_len: int = 500) -> list[str]:
    """长文本按句号切成段落，每段不超过 max_len 字。

    - 句号集合包含：。！？；!?
    - 先按句末标点切句，再贪心合并到 max_len；单句超长时按 max_len 硬切。
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_len:
        return [text]

    # 切句（保留句末标点）
    raw_sents = re.split(r"(?<=[。！？；!?])", text)
    sents = [s.strip() for s in raw_sents if s.strip()]

    if not sents:
        # 无标点纯长串，按 max_len 硬切
        return [text[i:i + max_len] for i in range(0, len(text), max_len)]

    result: list[str] = []
    buf = ""
    for s in sents:
        # 单句超长 → 先落当前 buf，再硬切该句
        if len(s) > max_len:
            if buf:
                result.append(buf)
                buf = ""
            for i in range(0, len(s), max_len):
                chunk = s[i:i + max_len]
                if chunk:
                    result.append(chunk)
            continue
        if len(buf) + len(s) > max_len:
            if buf:
                result.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        result.append(buf)
    return result
