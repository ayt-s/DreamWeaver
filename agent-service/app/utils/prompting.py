"""提示词组装工具：可灵式精细控制（运镜结构化 / 风格 / 负面词 / 元素绑定）。

agnes 官方没有 negative_prompt 字段（实测未知字段直接 400），
所以负面词一律折进提示词文本；风格同理。

agnes 官方推荐的提示词结构（见 agnes-video-2.5 文档 Prompting Recommendations）：
  1. 主体与场景  2. 动作与变化  3. 镜头语言  4. 视觉风格  5. 声音节奏  6. 一致性要求
本模块负责第 3/4/6 段的确定性拼接。

元素语义绑定：agnes reference 模式支持 <Picture N> / <Audio N> / <Video N> 占位符，
1-indexed 且各数组独立编号（images[0] = <Picture 1>）。
"""
from __future__ import annotations

# 景别 → 英文（对齐 agnes 文档 shot size 表述）
SHOT_SIZE_EN: dict[str, str] = {
    "远景": "wide shot",
    "全景": "full shot",
    "中景": "medium shot",
    "近景": "close-up shot",
    "特写": "extreme close-up",
}

# 机位 → 英文（镜头角度）
CAMERA_ANGLE_EN: dict[str, str] = {
    "平视": "eye-level angle",
    "俯拍": "high-angle shot",
    "仰拍": "low-angle shot",
    "航拍": "aerial shot",
    "过肩": "over-the-shoulder shot",
}

# 运镜 → 英文（对齐 agnes 文档 push / pull / pan / tilt / tracking / fixed）
CAMERA_MOVE_EN: dict[str, str] = {
    "固定": "static camera",
    "推近": "slow push-in",
    "拉远": "slow pull-out",
    "摇镜": "pan",
    "移镜": "tracking shot",
    "跟拍": "follow shot",
    "环绕": "orbit shot",
}

# 前端下拉选项源（与前端保持一致；后端做白名单校验用）
SHOT_SIZE_OPTIONS = list(SHOT_SIZE_EN)
CAMERA_ANGLE_OPTIONS = list(CAMERA_ANGLE_EN)
CAMERA_MOVE_OPTIONS = list(CAMERA_MOVE_EN)

# === 图片接口的画幅/尺寸白名单（官方 agnes-image-2.5-flash 文档的 supported values）===
# 与视频的 aspect_ratio 不同：图片多支持 2:3 / 3:2，且不接受 auto。
IMAGE_RATIOS: tuple[str, ...] = ("1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9")
# 输出尺寸档（1K/2K/3K/4K）；官方也接受 1024x768 这类精确值，但会被「就近归一化」，
# 所以只认档位，避免出现「以为给了 1920x1080，实际拿到 1312x736」的误解。
IMAGE_SIZE_TIERS: tuple[str, ...] = ("1K", "2K", "3K", "4K")
# 视频分辨率档（720P/960P/2K；Flash 硬限 720P）
VIDEO_SIZE_TIERS: tuple[str, ...] = ("720P", "960P", "2K")


def normalize_image_ratio(value: object, default: str = "16:9") -> str:
    """清洗出图画幅为 agnes 认的 ratio（白名单外回落默认，绝不透传脏值）。

    ★ 为什么必须有这个函数：**出图请求此前完全不传画幅**，服务端按默认给 1:1 ——
    2026-09-18 实测项目真实产物 18/18 全是 1024x1024 正方形，而视频链路是 16:9。
    画幅值来自前端下拉与段配置，可能是全角冒号「：」或带空格，
    而 agnes 对非法取值是**直接 400**（一次 400 = 一张图白等一轮重试），所以归一化后再发。
    """
    s = str(value or "").strip().replace("：", ":").replace("／", "/")
    return s if s in IMAGE_RATIOS else default


def normalize_image_size(value: object, default: str = "2K") -> str:
    """清洗出图尺寸档（1K/2K/3K/4K）；未知值回落默认，避免 400 或静默归一化。"""
    s = str(value or "").strip().upper()
    return s if s in IMAGE_SIZE_TIERS else default


def normalize_video_size(value: object, default: str = "720P") -> str:
    """清洗视频分辨率档（720P/960P/2K）；未知值回落默认。"""
    s = str(value or "").strip().upper()
    return s if s in VIDEO_SIZE_TIERS else default



def camera_phrase(spec: object) -> str:
    """把结构化运镜 spec 拼成英文提示词片段。

    spec 形状：{"shot_size": "远景", "angle": "俯拍", "movement": "推近"}。
    非法/空值忽略；全空返回空串。白名单外的值丢弃（防注入脏值进提示词）。
    """
    if not isinstance(spec, dict):
        return ""
    parts: list[str] = []
    for key, table in (
        ("shot_size", SHOT_SIZE_EN),
        ("angle", CAMERA_ANGLE_EN),
        ("movement", CAMERA_MOVE_EN),
    ):
        value = str(spec.get(key) or "").strip()
        if value and value in table:
            parts.append(table[value])
    return ", ".join(parts)


def normalize_camera_spec(spec: object) -> dict:
    """清洗运镜 spec：只保留白名单内的键值，返回规范 dict（可能为空）。"""
    if not isinstance(spec, dict):
        return {}
    out: dict[str, str] = {}
    for key, table in (
        ("shot_size", SHOT_SIZE_EN),
        ("angle", CAMERA_ANGLE_EN),
        ("movement", CAMERA_MOVE_EN),
    ):
        value = str(spec.get(key) or "").strip()
        if value and value in table:
            out[key] = value
    return out


def build_cn_description(
    base_parts: list[str],
    style_prompt: str = "",
    negative_prompt: str = "",
) -> str:
    """拼中文描述（供 LLM 翻译）：主体/动作 → 风格 → 负面词。

    风格与负面词一并进入翻译，保证英文产物里两者都被表达
    （agnes 无独立字段，只能写进提示词正文）。
    """
    parts = [str(p).strip() for p in base_parts if str(p or "").strip()]
    style = str(style_prompt or "").strip()
    if style:
        parts.append(f"画面风格：{style}")
    negative = str(negative_prompt or "").strip()
    if negative:
        parts.append(f"避免出现：{negative}")
    return "，".join(parts)


def build_reference_bindings(
    bindings: object,
    ref_images: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """元素语义绑定 → (角色定义句, 一致性要求句)。

    输入 shapes（兼容三种）：
      1. [{"name": "我", "image_index": 2}, {"name": "摩托车", "image_index": 3}]
      2. [{"name": "我", "imageIndex": 2}]  # Java/前端透传为 camelCase
      3. [{"name": "我", "imageUrl": "https://…/a.png"}]  # ← 推荐：按 url 现算编号

    **为什么推荐 shape 3（2026-09-17 修的真实缺陷）**：`<Picture N>` 的编号此前完全取自
    载荷、**从不与实际参考图数组核对** —— 而前端组装每段 `reference_images` 时是
    `[本段自己的图, ...锚定图]`，某段没有自己的图时数组整体前移一位，于是
    「"陈浔" refers to <Picture 2>」实际指向了第 1 张 → **绑错对象**。
    给了 `ref_images` 就按 url 在**这一段真实数组**里的位置现算；找不到就跳过该绑定
    （顺带消灭「被 5 张上限截断后仍被 <Picture N> 悬空引用」）。

    不传 `ref_images` 时退回用法 1/2（保持旧行为，向后兼容）。
    返回 ([...], [...])，调用方按位置插入提示词。
    """
    if not isinstance(bindings, list):
        return [], []
    defines: list[str] = []
    consistency: list[str] = []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        # 优先按 url 现算；没有 url 才退回调用方给的编号
        raw_url = str(item.get("imageUrl") or item.get("image_url") or "").strip()
        idx = 0
        if raw_url and ref_images is not None:
            try:
                idx = list(ref_images).index(raw_url) + 1  # 1-indexed，与 agnes 对齐
            except ValueError:
                idx = 0  # 这一段里没有这张图（被截断 / 本段无关）→ 不产生悬空引用
        else:
            raw_index = item.get("image_index", item.get("imageIndex", 0))
            try:
                # 前端传 1-based 图片编号（<Picture N> 语义），与 agnes 对齐
                idx = int(raw_index)
            except (TypeError, ValueError):
                continue
        if idx < 1:
            continue
        defines.append(f'"{name}" refers to <Picture {idx}>')
        consistency.append(name)
    if not defines:
        return [], []
    role_clause = "In this shot: " + "; ".join(defines) + "."
    keep_clause = ""
    if consistency:
        keep_clause = (
            "Keep the appearance of "
            + ", ".join(consistency)
            + " exactly consistent with the referenced images."
        )
    return [role_clause], [keep_clause] if keep_clause else []
