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

import re

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
# 视频分辨率档（官方 2026-09-18 文档：agnes-video-2.5 的 size 只支持 "720P" / "2K"；
# Flash 固定 "720P"，传别的档 400 `size must be 720P`）。
# ⚠️ 这里原本还有 "960P"（早期文档写过），现已从白名单移除：留着它等于把 960P **透传**
#    给上游换一个 400，而本文件的原则是「未知值回落默认，别让一个档位参数打挂整次生成」。
VIDEO_SIZE_TIERS: tuple[str, ...] = ("720P", "2K")


# 「一人一牛跪坐门外」这类**主体短语**：出现在**场景原文**里，而场景只该描述环境。
# 识别形状 = 数量词 + 主体量词/人，或泛称主体词。
# ⚠️ 泛称词刻意**不含「村民」**：「村民宴会场地」是地点名，误删会丢掉场景本身。
# ⚠️ composer.py 里另有一个 `_SUBJECT_COUNT_RE`（管 **[镜头] 段** 的「双人并排」这类构图措辞）
#    —— 职责不同，不要合并。
SCENE_SUBJECT_RE = re.compile(
    r"[一二两三四五六七八九十百千数几半][个位名头只匹条人]|人群|众人|人们|满村"
    r"|少年|少女|孩童|孩子|老者|老人|男子|女子"
)


def strip_subject_clauses(text: str, names: tuple[str, ...] | list[str] = ()) -> str:
    """从**场景描述**里剥掉主体子句，只留环境（2026-09-19）。

    ★ 为什么要这个函数（实测，非推测）：场景锚图是按场景描述生成的，而描述里常写着
      「**一人一牛**跪坐门外」这种主体短语 —— 于是**锚图里把人和牛一起画了进去**。
      那张锚图之后会被当**参考图**喂给每一镜的首帧出图，模型就把锚图里的主体**再画一遍**：
      实测废墟镜「带锚定图 人多 4/5 vs 不带 0/10」，而同场景的废墟锚图里正好有人和牛
      （对照：山洞锚图是空景 → 那一镜从不丢主体、也不多画）。
      提示词里已有「空场景无角色」的抽象约束，但**模型听具体的描述、不听抽象约束** ——
      所以必须把描述里的主体子句拿掉。这就是同一套正则从"合成层"挪到"锚图生成层"的原因。

    只剥主体，环境子句一个不动；`names` 给角色名（剥掉点名角色的子句）。
    兜底：剥完太短（<6 字）就**原文返回** —— 宁可留着冗余，也不能交出一个空描述。
    """
    src = (text or "").strip()
    if not src:
        return text
    kept: list[str] = []
    for clause in re.split(r"[，、；]", src):
        clause = clause.strip()
        if not clause:
            continue
        if SCENE_SUBJECT_RE.search(clause):
            continue
        if any(n and n in clause for n in names):
            continue
        kept.append(clause)
    if not kept:
        return src
    out = "，".join(kept)
    return out if len(out) >= 6 else src


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


# agnes 图像接口的 seed 取值范围（**上游实测**）：2026-09-18 传 1000/1001 一律
# 400 {"message": "seed 必须在 -1 到 999 之间"}；-1 = 随机。
# ⚠️ 视频接口的 seed 范围**没有实测过**，别照抄这个区间去夹视频的 seed。
IMAGE_SEED_MIN, IMAGE_SEED_MAX = -1, 999


def normalize_image_seed(value: object, default: int = -1) -> int:
    """把出图 seed 收敛到 agnes 认的范围（辅助参数不该把整次出图打挂）。

    - `None` / 空串 / 非数字 → `default`（-1 = 上游随机）
    - 越界 → **夹到边界**（而不是回落随机：夹过去至少仍是「一个确定的种子」，
      调用方的可复现意图不会被静默改成另一种语义）

    为什么要这个函数：网关原来直接 `int(seed)` 透传，越界会被上游 400 ——
    一次 400 = 一张图白等一轮重试（与 ratio / size 当初踩的是同一个坑）。
    """
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return max(IMAGE_SEED_MIN, min(IMAGE_SEED_MAX, n))


def normalize_video_size(value: object, default: str = "720P") -> str:
    """清洗视频分辨率档（720P/2K）；未知值回落默认。"""
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
