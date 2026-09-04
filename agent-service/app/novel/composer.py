"""Prompt 拼装器（纯模板，无 LLM）。

- compose_image_prompt：给文生图模型看的图像 prompt。
- compose_video_prompt：给文生视频模型看的视频 prompt（比 image 多一句时长提示）。

红线：严禁面部特写 / 严禁人物居中占比较大 / 4K 超高清 / 16:9 / 主体清晰居中 / 四周安全边距 / 无字幕无水印。
"""
from __future__ import annotations

# 全局红线，追加到每个 prompt 末尾
_IMAGE_RED_LINES = (
    "严禁面部特写；"
    "严禁人物居中占比较大；"
    "4K 超高清；"
    "16:9 画幅；"
    "主体清晰居中；"
    "四周安全边距；"
    "无字幕无水印；"
    "无文字乱码。"
)


def _format_characters(seg: dict, analysis: dict | None = None) -> str:
    """把本段角色名拼成 '角色名(特征)' 列表。

    如果传了 analysis 且有角色特征卡，就用『名字(特征)』锁定描述；
    没有特征卡时只列名字。
    """
    names = seg.get("characters") or []
    if not names:
        return "无具体人物"
    card = (analysis or {}).get("characters") or {}
    parts = []
    for name in names:
        if name in card:
            parts.append(f"{name}（{card[name]}）")
        else:
            parts.append(name)
    return "、".join(parts)


def compose_image_prompt(seg: dict, style: str, analysis: dict | None = None) -> str:
    """拼一段图像生成 prompt。

    结构：镜头语言 → 场景 → 角色（含特征卡锁定）→ 情节 → 风格 → 红线
    """
    camera = seg.get("camera", "")
    scene = seg.get("scene", "")
    characters = _format_characters(seg, analysis)
    plot = seg.get("plot", "")
    mood = seg.get("mood", "")

    prefix = f"{camera}：" if camera else ""
    body = (
        f"{prefix}"
        f"场景：{scene}；"
        f"角色：{characters}；"
        f"情节：{plot}；"
        f"情绪：{mood}；"
        f"风格：{style}；"
    )
    return body + _IMAGE_RED_LINES


def compose_video_prompt(
    seg: dict, style: str, analysis: dict | None = None
) -> str:
    """视频 prompt：在 image prompt 基础上追加以秒为单位的时长提示。"""
    base = compose_image_prompt(seg, style, analysis)
    seconds = int(seg.get("seconds", 5))
    return f"时长 {seconds} 秒。{base}"
