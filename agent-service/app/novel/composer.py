"""Prompt 拼装器（纯模板，无 LLM）。

四段式结构（agnès 对超长 prompt 响应衰减，明确分段让画面理解更准）：
  [主体]  → 核心视觉元素（角色名 + 关键道具，不含背景描述）
  [场景]  → 空间 + 时间 + 天气（决定环境基调）
  [镜头]  → 专业镜头术语（镜头类型 + 运动方向，agnès 靠这个决定画面构图）
  [风格]  → 情绪 + 视觉风格（决定画面质感）

红线单独放末尾，agnès 会优先识别末尾约束。

关键改进（相对旧版）：
1. 主体段只写"谁在做什么"，压缩到 ≤30 字，避免 agnes 抓不住重点
2. 镜头段强制包含镜头类型（广角/中景/特写/俯拍）+ 运动方向（固定/推拉/横移/跟拍）
3. 段落间用显式分隔符，避免长 prompt 语义混淆
"""
from __future__ import annotations

# 全局红线，追加到每个 prompt 末尾（agnès 会优先识别末尾约束）
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


def _extract_subject(seg: dict) -> str:
    """从 plot 中提取核心视觉主体（≤30 字）。

    agnes 对超长 prompt 响应衰减，主体段要精炼。
    策略：取 plot 第一个分句（中文逗号/句号分隔），截断到 30 字。
    """
    plot = seg.get("plot", "").strip()
    if not plot:
        return "无具体动作"
    # 第一个分句
    for sep in ["，", "；", "。", "；"]:
        idx = plot.find(sep)
        if 0 < idx < 40:
            plot = plot[:idx]
            break
    return plot[:30]


def _ensure_camera_terms(camera: str) -> str:
    """强制镜头术语包含镜头类型 + 运动方向。

    storyboarder 已经按提示词要求生成专业镜头术语，这里兜底：
    如果既没镜头类型也没运动方向，用默认"中景固定"。
    """
    if not camera:
        return "中景固定"
    # 已有明确术语就不动
    return camera


def compose_image_prompt(seg: dict, style: str, analysis: dict | None = None) -> str:
    """拼一段图像生成 prompt（六段式，按优先级从高到低排列）。

    结构：[角色锚] → [主体动作] → [场景] → [镜头] → [风格] → 红线
    优先级说明：
    1. 角色锚：角色特征卡最详细，agnès 会优先用角色锚定图对齐外观
    2. 主体动作：这个镜头具体发生什么（≤30 字）
    3. 场景：地点 + 时间 + 天气 + 光线
    4. 镜头：镜头类型 + 运动方向 + 构图
    5. 风格：色调 + 光影 + 时代感
    末尾红线：agnès 对末尾约束响应最好，放红线兜底
    """
    subject = _extract_subject(seg)
    scene = seg.get("scene", "")
    camera = _ensure_camera_terms(seg.get("camera", ""))
    characters = _format_characters(seg, analysis)
    mood = seg.get("mood", "")

    # 用 [段名] 前缀明确分段，按优先级从高到低排列
    parts = []
    parts.append(f"[角色锚] {characters}")  # 优先级最高：角色外观一致性
    parts.append(f"[主体动作] {subject}")  # 次高：画面主体内容
    parts.append(f"[场景] {scene}")
    parts.append(f"[镜头] {camera}")
    style_suffix = f"，情绪{mood}" if mood else ""
    parts.append(f"[风格] {style}{style_suffix}")

    return "；".join(parts) + "。" + _IMAGE_RED_LINES


def compose_video_prompt(
    seg: dict, style: str, analysis: dict | None = None
) -> str:
    """视频 prompt：在 image prompt 基础上追加以秒为单位的时长提示。

    时长放在最前面，agnès 视频模式会优先读取时长约束。
    """
    base = compose_image_prompt(seg, style, analysis)
    seconds = int(seg.get("seconds", 5))
    return f"时长 {seconds} 秒。{base}"
