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


# ============================================================================
# 视频提示词规范（Agnes Video 2.5 提示词模板指南 v1.0 · 2026-09-08）—— 2026-09-24 落地
# ============================================================================
#
# 文档公式：提示词 = 【参考素材说明】+【核心创意】+【画面过程说明（按时间轴分段，正向+反向）】
#
# ★ 为什么必须单开一个字段（`video_prompt_en`）而不是改 `prompt_en`：
#   标准链路上 `prompt_en` 同时喂给 `image_generator` 出图（nodes/image.py:396）——
#   把「0-2 秒…/2-5 秒…」和声音段塞进去，对图像模型全是噪音。图像提示词与视频
#   提示词本来就是两种规范（Java 侧一直有 imagePrompt/videoPrompt 两个字段）。
#
# ★ 落地前的实测基线（2026-09-24，读 agent.log 里 3 条真实 submit_video payload）：
#   - 提交的提示词 = 一段静态画面描述 + 运镜 + 风格 + 红线，**没有任何时间轴**；
#     5 秒里发生什么完全由模型自己编。
#   - 提示词里**一个字的声音指令都没有**，而 ffprobe 三个分段全是 h264+aac
#     —— 即每段的 BGM 都是模型自己配的，段段不同；拼接（acrossfade）救不了
#     「每段换一首曲子」。
#   - 出现过中文残留（"a mood of panic and绝望"）：翻译口径没有「必须全英文」约束。

# 时长/画幅头（文档 §2.2「核心创意」要求一句话锁定全片信息，含时长与画幅）。
_ORIENTATION_CN = {"16:9": "横版", "9:16": "竖版", "1:1": "方形", "4:3": "横版", "3:4": "竖版"}
_ORIENTATION_EN = {"16:9": "horizontal", "9:16": "vertical", "1:1": "square",
                   "4:3": "horizontal", "3:4": "vertical"}


def video_duration_head(seconds: object, aspect_ratio: object, lang: str = "en") -> str:
    """「5 秒，16:9 横版」这类时长+画幅头（文档要求在正文里明写，不只在 API 参数里）。"""
    try:
        sec = int(float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        sec = 5
    ratio = str(aspect_ratio or "16:9").strip() or "16:9"
    if lang == "cn":
        return f"{sec} 秒，{ratio} {_ORIENTATION_CN.get(ratio, '')}".strip()
    return f"{sec} seconds, {ratio} {_ORIENTATION_EN.get(ratio, '')}".strip()


# 视频提示词的**声音段**：文档 §2.3「反向 = 必须写」那一句。
# BGM 开关打开（bgm=True）时**不追加**：让模型按其默认行为配乐
# （文档提醒「想要音乐」与「不要 BGM」两句同时出现是自相矛盾的，只能留一句）。
SOUND_NO_BGM_EN = ("No extra background music; keep only natural ambient sounds "
                   "and action sounds.")
SOUND_NO_BGM_CN = "不要额外添加背景音乐，只保留环境音与动作音。"


def sound_clause(bgm: bool) -> str:
    """视频提示词的声音排除句（英文）。BGM 开关打开时返回空串。"""
    return "" if bgm else SOUND_NO_BGM_EN


# keyframe（首帧锁定）模式下补的一句：文档 §4.2「双张图（首帧+尾帧）：Agnes 不会自动
# 加切镜，只补两帧之间的动作、光影、声音」——我们目前只给首帧，同理只该往后延展，
# 不该让模型重构图/重计时（reference 模式的语义差别见 gateway/agnes.py:446-448）。
FIRST_FRAME_EN = ("Use the given first frame as the literal opening frame; extend it forward "
                  "with motion, light and sound only — do not re-frame or re-time the shot.")


# 视频改写模板（送给 LLM 的指令 + 中文分镜描述）。
# 在 translate_to_en 之上做一次「按视频规范改写」，而不是逐字翻译：
# 时间轴/声音这两段在中文侧**根本不存在**，必须由这一步生成。
#
# ⚠️ 运镜术语走 `{camera}` 注入**模板内部**，不在生成后追加 ——
#   实测（2026-09-24 首轮真实产出）事后追加会得到
#   「... watermark., medium shot, slow push-in」：既与首句里的运镜重复，
#   又把排除句挤到中间（文档明确末尾约束权重最高）。交给模型整合进首句即可。
VIDEO_PROMPT_TEMPLATE = """Rewrite the Chinese shot description below into ONE English prompt
for the Agnes Video 2.5 model, following the model's official prompt guide.

Required shape (plain text, no headings, no bullet points, no markdown):
1. Start with the shot length and frame: "{duration}".
2. Then one sentence locking the whole shot: subject + place + action + genre/style + camera move.
3. Then what happens inside the shot, split into 2-3 time phases ("0-2s: ...", "2-{seconds}s: ...").
   Each phase must state something VISIBLE: subject action, environment, light, camera movement.
   Concrete visible pictures only; no abstract metaphor or mood-only wording.
4. Then the sound of the shot: ambient sound and action sound, PLUS the dialogue given below.
   The dialogue must be quoted VERBATIM — never translate, rewrite or shorten it — and placed
   in the time phase where it is spoken; that phase must be long enough for the line to be said
   at natural speed. Voice-over / offscreen lines must be marked as voice-over (not lip-synced).
   If the dialogue line says "none", state that there is no dialogue.
5. One continuous take; never write a multi-shot / cut structure.
6. Keep characters, costume, hairstyle and scene wording faithful to the Chinese text.
   Do NOT invent characters, props, events or dialogue that are not in it.
7. End with what must NOT appear: deformed hands, extra limbs, on-screen text, watermark.
8. Output English only — EXCEPT the dialogue line, which must be reproduced character for
   character exactly as given (never translated). Speaker ROLES must be English
   (e.g. "voice-over" instead of 画外音); character names may stay as given.

Camera (use this exact English wording inside sentence 2, only once): {camera}

Dialogue — INPUT SPEC, describe it in the prompt; never copy this block or its labels:
{dialogue_hint}

Chinese shot description:
{text}
"""


def build_video_rewrite_input(
    cn_description: str, seconds: object, aspect_ratio: object, camera_en: str = "",
    dialogue: str = "", speaker: str = "",
) -> str:
    """拼出「视频改写」的 LLM 输入（模板 + 时长/画幅 + 运镜 + 台词 + 中文描述）。纯拼接。"""
    camera = str(camera_en or "").strip() or "as described in the Chinese text below"
    try:
        sec = int(float(seconds))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        sec = 5
    return VIDEO_PROMPT_TEMPLATE.format(
        duration=video_duration_head(seconds, aspect_ratio),
        seconds=sec,
        camera=camera,
        dialogue_hint=dialogue_hint(dialogue, speaker),
        text=cn_description or "",
    )


# ============================================================================
# 台词（Agnes Video 2.5 文档 §2.3「台词对齐原则」+ 速查表第 2 条「台词 = 原文」）
# ============================================================================
#
# ★ 2026-09-24 落地前的实测：全链路**没有台词词位** ——
#   `novel/storyboarder.py` 的 schema 与 `nodes/script.py` 的模板都没有 dialogue
#   字段（后者只在硬约束里写过一句「可以补充台词」），于是小说里的对话被压进
#   `plot`/`visual` 的叙述文字里，模型只能自己编口型对白。
#   文档明说「很多口型问题都来自台词与镜头时长不对齐」。

# 台词角色标注：画内 vs 画外（文档 §2.3「画内/画外说话人」要求写清）
OFFSCREEN_MARKERS = ("画外音", "旁白", "画外", "offscreen", "voice-over", "voiceover")


def is_offscreen(speaker: str) -> bool:
    """说话人是不是画外（画外音/旁白）。文档要求画内外必须写清。"""
    s = str(speaker or "").strip().lower()
    return any(m.lower() in s for m in OFFSCREEN_MARKERS)


def dialogue_hint(dialogue: object, speaker: object = "") -> str:
    """台词注入模板的那一行（英文，供改写 LLM 逐字保留）。

    ⚠️ 2026-09-24 实测教训：**这一行曾被模型当成正文整段抄进输出**
    （"Voice-over / offscreen, NOT lip-synced on screen — speaker: 画外音; quote
    verbatim: …" 原样出现在提示词里，还把中文标签留在了英文提示词中）。
    所以这里给的是**输入规格**形状：明确「输出里该写成的样子」，
    并在模板里声明这是 spec、不许照抄。
    """
    text = str(dialogue or "").strip()
    if not text:
        return ("NONE — this shot has no dialogue. Do not invent dialogue, and do not show "
                "anyone speaking or lip-syncing.")
    who = str(speaker or "").strip() or "an unnamed character"
    if is_offscreen(who):
        return (f'LINE (reproduce character for character, never translate): "{text}"\n'
                f'SPEAKER: {who} — voice-over / offscreen: the speaker is NOT shown talking '
                f'and must not be lip-synced. Write it as: a voice-over says: "{text}"')
    return (f'LINE (reproduce character for character, never translate): "{text}"\n'
            f'SPEAKER: {who} — on screen, visible lip-sync. '
            f'Write it as: {who} says: "{text}"')


def dialogue_clause_cn(dialogue: object, speaker: object = "") -> str:
    """中文版台词段（落库/预览用的中文视频提示词）。无台词返回空串。"""
    text = str(dialogue or "").strip()
    if not text:
        return ""
    who = str(speaker or "").strip()
    mark = "画外音" if is_offscreen(who) else "画内"
    name = who or "角色"
    return f"【台词】{name}（{mark}）说：「{text}」（原文照读，按镜头时长说完，不要增删）"
