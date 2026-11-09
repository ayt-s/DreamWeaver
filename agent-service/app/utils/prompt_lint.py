"""视频提示词机检 · Agnes 2.5 语法适配版（只报不改 · WARN 级）。

规则来源
--------
判据含义来自「漫剧老李 short-drama-director」（MIT）的 C 系机检。**不能直接调用它的
`validate_prompt.py`**：那脚本只认「七段式栏位 + `00:00-00:05 [镜头1] 景别 · 视角 ·
运镜`」的 Seedance 语法，拿我们真实的 Agnes 英文段落稿去跑，它会直接以
`exit code 3 = 非稿件` 退出，一条判据都不会执行（2026-09-24 实测）。

所以这里只搬**判据的含义**，判据本身按我们的语法重写：

1. `lint_cn_shot()` —— 中文层语义判据。作用在**改写前的中文描述**上：中文关键词表
   可以原样复用（C16/C18/C19 那批本来就是「视频模型会读错的中文写法」）。
2. `lint_video_prompt_en()` —— 英文层格式判据。作用在**真正提交给 agnes 的那条正文**上。

设计铁律
--------
**只 WARN，不阻断提交。** 判据是关键词启发式，必然有误报；阻断会把好稿拦死。
结果写进日志与段落库（`segments_json.prompt_lint`），供人工复核与后续迭代。

刻意**不做**的判据（判不了 / 会误杀）：
- C1/C2 时长与切镜数：我们的时长由 `seconds` 参数与改写模板保证（4~12s 由网关校验）。
- C11 接续状态 / C7 七段式：属于 Seedance 的格式契约，与 Agnes 无关。
- C13 镜长雷同：我们是「一段一镜」，没有多镜时长可比。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = ["lint_cn_shot", "lint_video_prompt_en", "lint_segment", "has_fail"]


# 判定为「有台词/在看人」的锚点词：出现任一即认为视线/表演已有交代
_GAZE_ANCHORS = ("目光", "视线", "眼神", "看向", "望", "盯", "对视")
# 面部动作锚点（FACS 化的最低要求：写具体部位而不是结果词）
_FACE_ANCHORS = ("眉", "眼", "嘴", "下颌", "颧", "嘴角", "目光", "瞳孔", "鼻翼")
# 否定式写法（模型对词根敏感，被禁词根反被当画面词渲染）
_NEGATION_PATTERNS = (
    "不发光", "不透光", "不悬浮", "面无笑意", "不带笑意", "不要出现", "不要有", "不能有",
    "没有表情", "毫无表情",
)
# 空间级事件：特写装不下，模型只能丢人物保空间
_SPACE_EVENTS = ("房顶", "屋顶", "全屋", "整屋", "院子", "窗外", "整个房间", "全景", "满屋")
_CLOSEUPS = ("特写", "大特写", "近景特写")
# 空的情绪结果词（应改写为具体肌肉动作）
_VAGUE_EMOTIONS = ("开心", "难过", "愤怒", "邪魅", "震惊", "悲伤", "痛苦", "惊恐", "狂喜")
# 姿态回退
_POSE_UP = ("坐起", "撑起", "起身", "站起", "弹起", "爬起")
_POSE_DOWN = ("躺", "卧", "蜷", "瘫")
_POSE_TRANSITION = ("跌回", "躺回", "倒回", "摔回", "滑落", "瘫下", "倒下去")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_QUOTED_RE = re.compile(r'"[^"]*"|“[^”]*”|「[^」]*」')
_EN_HEAD_RE = re.compile(r"^\s*\d+\s*(?:seconds?|s)\b[^.]{0,30}\d+\s*:\s*\d+", re.I)
_EN_TIMELINE_RE = re.compile(r"\b0\s*-\s*\d+(?:\.\d+)?\s*s?\s*:", re.I)
_EN_SOUND_RE = re.compile(
    r"(ambient|background sound|sound of|sounds? of|audio|no dialogue|silence|sfx)", re.I)
_EN_EXCLUSION_RE = re.compile(
    r"(no |not |without |must not|should not|never )", re.I)
# 我们自己的确定性后缀（声音排除句 / 首帧说明 / 红线）——判「有没有声音描述」时必须先剔除，
# 否则它们里面的 `sounds` / `watermark` 会满足判据，把「整段没写声音」漏成通过。
_EN_DETERMINISTIC_RE = re.compile(
    r"(no extra background music|keep only natural ambient|deformed hands|extra limbs|"
    r"on-screen text|watermark|do not re-frame|literal opening frame|first frame)", re.I)
# 元信息泄漏：模型把「输入规格/指令」的指代写进正文（真机实测：`as described in the
# Chinese text below`）。这类句子对视频模型是无意义噪音，且指代的文本并不存在于输出里。
_EN_META_RE = re.compile(
    r"(as (?:described|mentioned|stated|specified)\b[^.]{0,40}"
    r"(?:chinese|text|description|prompt|above|below)"
    r"|the chinese (?:text|description)"
    r"|(?:text|description|prompt)\s+(?:above|below)"
    r"|translated from"
    r"|as per the (?:description|prompt|instruction)"
    r"|nothing may come after"
    r"|(?:this|the) (?:final|last) sentence"
    r"|follow (?:the|these) (?:instructions?|rules?))", re.I)


def _issue(code: str, msg: str, rule: str, level: str = "WARN") -> dict[str, str]:
    return {"code": code, "level": level, "rule": rule, "msg": msg}


def _hit(text: str, words: Iterable[str]) -> list[str]:
    return [w for w in words if w in text]


def _dialogue_chars(dialogue: str) -> int:
    """台词有效字数（去掉标点与空白）——用于「台词能不能在段时长内说完」。"""
    return len(re.sub(r"[\s\W_]+", "", dialogue or "", flags=re.UNICODE))


def lint_cn_shot(
    cn_text: str,
    *,
    dialogue: str = "",
    seconds: int | float | None = None,
    prev_cn_text: str = "",
    camera_spec: Any = None,
) -> list[dict[str, str]]:
    """中文层语义机检（改写前的描述）。返回 WARN 列表，不抛异常。"""
    text = str(cn_text or "").strip()
    issues: list[dict[str, str]] = []
    if not text:
        return issues

    # C19a 裸禁令词污染
    neg = _hit(text, _NEGATION_PATTERNS)
    if neg:
        issues.append(_issue(
            "CN_BARE_NEGATION",
            f"否定式写法「{'/'.join(neg)}」：模型对词根敏感，被禁概念反会被渲染；"
            "改正向描述（材质本色 / 具体肌肉动作 / 画面主体）。",
            "C19a"))

    # C18c 视线黏道具
    prop = _hit(text, ("把玩", "摩挲", "摆弄", "举向", "捧向", "翻看"))
    if prop and not _hit(text, _GAZE_ANCHORS):
        issues.append(_issue(
            "CN_GAZE_ON_PROP",
            f"有道具动作「{'/'.join(prop)}」但没写视线落点 —— 模型默认让角色盯着道具说话；"
            "补「目光落在对方脸上」。",
            "C18c"))

    # C18d 特写承载空间级事件
    closeup = _hit(text, _CLOSEUPS)
    space = _hit(text, _SPACE_EVENTS)
    if closeup and space:
        issues.append(_issue(
            "CN_SPACE_IN_CLOSEUP",
            f"景别「{'/'.join(closeup)}」承载空间事件「{'/'.join(space)}」 —— 特写装不下，"
            "模型会丢人物保空间；改用中景/全景承载。",
            "C18d"))

    # C16c / C25a 空的情绪结果词
    vague = _hit(text, _VAGUE_EMOTIONS)
    if vague and not _hit(text, _FACE_ANCHORS):
        issues.append(_issue(
            "CN_VAGUE_EMOTION",
            f"情绪结果词「{'/'.join(vague)}」但没有面部动作 —— 模型只会给一张通用脸；"
            "改成具体部位动作（双唇抿紧 / 下颌绷紧 / 眼睑下垂）。",
            "C16c"))

    # C18b 姿态回退（需要上一镜）
    prev = str(prev_cn_text or "")
    if prev:
        prev_up = _hit(prev, _POSE_UP)
        now_down = _hit(text, _POSE_DOWN)
        if prev_up and now_down and not _hit(text, _POSE_TRANSITION):
            issues.append(_issue(
                "CN_POSE_REGRESSION",
                f"上一镜已「{'/'.join(prev_up)}」，本镜又出现「{'/'.join(now_down)}」且没写过程 —— "
                "姿态无声回退会穿帮；补「跌回/躺回」等交代，或保持原姿态。",
                "C18b"))

    # 台词时长对齐（文档 §2.3：中文口播约每字 0.2 秒）
    if dialogue and seconds:
        chars = _dialogue_chars(dialogue)
        budget = float(seconds) / 0.2
        if chars > budget:
            issues.append(_issue(
                "CN_DIALOGUE_TOO_LONG",
                f"台词 {chars} 字，{seconds}s 的口播预算约 {int(budget)} 字（每字 0.2s）—— "
                "说快了会含混、口型对不上；拆句或延长时长。",
                "C27b"))

    return issues


def lint_video_prompt_en(prompt: str, *, seconds: int | float | None = None) -> list[dict[str, str]]:
    """英文层格式机检（真正提交给 agnes 的那条正文）。返回 WARN 列表。"""
    text = str(prompt or "").strip()
    issues: list[dict[str, str]] = []
    if not text:
        return [_issue("EN_EMPTY", "视频提示词为空。", "spec")]

    # 时长 + 画幅头（Agnes 规范第一句：`5 seconds, 16:9 horizontal.`）
    if not _EN_HEAD_RE.search(text):
        issues.append(_issue(
            "EN_NO_HEAD",
            "开头缺「时长 + 画幅」（如 `5 seconds, 16:9 horizontal.`）—— 文档要求首句给时长与画幅。",
            "spec-head"))

    # 时间轴分段
    if not _EN_TIMELINE_RE.search(text):
        issues.append(_issue(
            "EN_NO_TIMELINE",
            "没有时间轴分段（`0-2s: … 2-5s: …`）—— 5 秒内发生什么会完全交给模型。",
            "spec-timeline"))

    # 声音段
    # ★ 真机漏检（2026-09-24 第 3 轮）：正文里**整段没有声音描述**，只有末尾那句
    #   `No extra background music; keep only natural ambient sounds…`，而判据被
    #   那句排除句里的 `sounds` 满足 → 漏报。先剔掉确定性后缀再判。
    sentences = re.split(r"(?<=[.!?])\s+", text)
    body_no_suffix = " ".join(s for s in sentences if not _EN_DETERMINISTIC_RE.search(s))
    if not _EN_SOUND_RE.search(body_no_suffix):
        issues.append(_issue(
            "EN_NO_SOUND",
            "没有声音描述 —— 成片音轨会由模型自由发挥（末尾那句声音排除句不算描述）。",
            "spec-sound"))

    # 排除项必须在末位（末尾约束权重最高）
    tail = text[-160:]
    if not _EN_EXCLUSION_RE.search(tail):
        issues.append(_issue(
            "EN_TAIL_NOT_EXCLUSION",
            "最后一句不是排除/约束项 —— 声音排除句与「不许变形/水印」应留在末位。",
            "spec-tail"))

    # 中文残留（引号内是台词，允许；引号外必须全英文）
    outside = _QUOTED_RE.sub(" ", text)
    residue = _CJK_RE.findall(outside)
    if residue:
        issues.append(_issue(
            "EN_CJK_RESIDUE",
            f"引号外有 {len(residue)} 个中文字符（如「{''.join(residue[:6])}」）—— "
            "此前真机出过 `a mood of panic and绝望` 这类漏翻。",
            "spec-lang"))

    # 元信息泄漏（指令/输入规格被写成正文）
    meta = _EN_META_RE.search(text)
    if meta:
        issues.append(_issue(
            "EN_META_REFERENCE",
            f"正文里出现对输入/指令的指代「{meta.group(0)[:40]}」—— 模型把这句当时画面描述渲染，"
            "而它指代的文本并不在输出里；删掉，只留画面内容。",
            "spec-meta"))

    return issues


def lint_segment(
    *,
    cn_text: str,
    video_prompt_en: str,
    dialogue: str = "",
    seconds: int | float | None = None,
    prev_cn_text: str = "",
    camera_spec: Any = None,
) -> list[dict[str, str]]:
    """两层合一：中文语义 + 英文格式。它就是接入点要调的那个函数。"""
    return (
        lint_cn_shot(cn_text, dialogue=dialogue, seconds=seconds,
                     prev_cn_text=prev_cn_text, camera_spec=camera_spec)
        + lint_video_prompt_en(video_prompt_en, seconds=seconds)
    )


def has_fail(issues: Iterable[dict[str, str]]) -> bool:
    """当前全部判据都是 WARN，保留这个函数给将来真有 FAIL 级判据时用。"""
    return any(str(i.get("level", "")).upper() == "FAIL" for i in issues)
