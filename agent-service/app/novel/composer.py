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

import re

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


def _char_aliases(name: str) -> tuple[str, ...]:
    """角色的可匹配写法：全名 + 末尾 2 字做简称。

    storyboarder 写 [主体动作] 时不会总用全名：「大黑牛」常写成「黑牛」
    （「黑牛反刍保下些许大米」），所以光比全名会该留的没留住。
    """
    name = (name or "").strip()
    if not name:
        return ()
    if len(name) <= 2:
        return (name,)
    return (name, name[-2:])


# 裁剪时参与匹配的分镜文本字段。
#
# ★ 必须**含 camera**：镜头段会点名角色（「全景，王二站在门口」）。只扫 plot+scene 时，
#   名字只写在镜头里的角色会被静默踢出角色锚，而角色锚是面部一致性的唯一来源 ——
#   这是「本镜确实出现却没锁定长相」的收窄，比多画一个更难发现。
# ★ 刻意**不含** imagePrompt / videoPrompt：编排器先拼 imagePrompt、紧接着拼 videoPrompt，
#   把已生成的提示词也扫进来会让第二次拼装「自证命中」→ 角色锚退回全给，两段提示词口径不一致。
_SEG_TEXT_FIELDS = ("title", "plot", "scene", "camera", "mood", "angle", "movement")


def _mentioned_characters(seg: dict) -> list[str]:
    """本镜**真正被提到**的角色。

    ★ 为什么必须按镜裁剪：`[角色锚]` 里列了谁，agnès 就把谁都画出来 —— 而且
    「大黑牛」会被画成**两头**。2026-09-17 实测第一章 6 镜 18 张图无一例外，
    连 [主体动作] 明写「一人一牛」的那镜也是两头。角色锚不裁剪 = 每镜都在点单。

    保守策略（与前端 P0-3 同口径）：**一个都没匹配到就退回全给**。
    宁可多画一个，也不能把本该出场的角色漏掉 —— 漏角色比多画更难发现。
    """
    names = seg.get("characters") or []
    if not names:
        return []
    text = " ".join(str(seg.get(f) or "") for f in _SEG_TEXT_FIELDS)
    hit = [n for n in names if any(a and a in text for a in _char_aliases(n))]
    return hit or list(names)


# 动物 / 灵兽类角色的名字关键词 —— 决定它「进不进 [角色锚]」。
#
# ★ 实测（2026-09-17 同一镜 A/B/C 三组，各 3 张候选）：
#     A 角色锚+场景都提牛           → 每张 2 头牛
#     B 只删掉场景/镜头里的提法      → 每张仍是 2 头牛（说明「提两次」不是原因）
#     C 角色锚只留少年、牛只在场景里  → 每张 **1 人 1 牛** ✓ 正确
#   即：**大黑牛只要进 [角色锚]，模型就画两头**。推测是它的角色卡里
#   「左角已断…右角完整…两只铜铃般的大眼睛…四蹄粗壮」这种并列分句被当成了两个主体。
#   所以动物/灵兽不进角色锚，改用场景段里一句短的带出。
_ANIMAL_HINTS = (
    "牛", "兽", "马", "驴", "狼", "犬", "狗", "猫", "鸟", "鹰", "龙", "虎", "豹",
    "蛇", "熊", "鹿", "羊", "猪", "兔", "狐", "猴", "猿", "鼠", "鱼", "龟", "雀",
    "鹤", "灵兽", "妖兽", "妖", "宠",
)

# 人物卡里常见的词 —— 用来把「名字像动物、其实是人」的角色捞回来。
# 刻意不收单字「人」「老」这类过宽的词：动物的卡里「通人性」「老狗」都会被误命中。
_HUMAN_HINTS = (
    "少年", "少女", "青年", "中年", "老年", "老汉", "壮汉", "男子", "女子", "妇人",
    "男人", "女人", "男孩", "女孩", "孩童", "孩子", "娃", "岁", "男", "女",
    "哥", "姐", "弟", "妹", "叔", "婶", "婆", "爷",
)


def _is_animal(name: str, card: str = "") -> bool:
    """这个角色是不是动物/灵兽。**两条判据缺一不可。**

    2026-09-17 被既有测试抓出的假阳性：一个「黑牛（中年壮汉，络腮胡，戴斗笠）」的
    **人物**，只按名字判会被当成牛、从 [角色锚] 里踢出去，白白丢掉面部一致性。
    中国小说里拿动物词当人名/绰号太常见（黑牛、二狗、小猫），所以必须再看角色卡像不像人。

    只按名字判第一层、只扫卡判第二层，是为了避免另一种假阳性：
    人物的卡里出现「牛皮甲」「牧牛」这类词（纯扫卡会把人物判成动物）。
    """
    if not any(h in (name or "") for h in _ANIMAL_HINTS):
        return False
    return not any(w in (card or "") for w in _HUMAN_HINTS)


def _animal_names_from_analysis(analysis: dict | None) -> set[str] | None:
    """读 analyzer 的结构化字段：这本书里哪些角色是动物/灵兽。

    返回 **None** 与返回**空集**是两件事：
    - None = 分析结果里根本没有这个字段（老数据）→ 只能退回关键词猜；
    - 空集 = 分析器明确说了"没有非人角色"→ **不要再猜**，否则「黑牛」这种人类绰号
      又会被关键词表误伤（既有测试抓过这个假阳性）。
    两种写法都读：字段经过某些链路可能被 camel 化。
    """
    a = analysis or {}
    for key in ("animal_characters", "animalCharacters"):
        if key in a:
            v = a.get(key)
            if isinstance(v, (list, tuple, set)):
                return {str(x).strip() for x in v if str(x).strip()}
            return set()  # 字段在但形状异常 → 当作"没有动物"，不去猜
    return None


def _split_characters(seg: dict, analysis: dict | None = None) -> tuple[list[str], list[str]]:
    """把本镜角色拆成（人物, 动物/灵兽）。

    **优先用 analyzer 给的结构化判断**（`animal_characters`），它不挑名字怎么写 ——
    关键词表认不出的「饕餮」「麒麟」也能正确归到动物；老数据没有该字段时才退回猜。
    """
    card = (analysis or {}).get("characters") or {}
    declared = _animal_names_from_analysis(analysis)
    kept = _mentioned_characters(seg)

    def is_animal(n: str) -> bool:
        if declared is not None:
            return n in declared
        return _is_animal(n, card.get(n, ""))

    return [n for n in kept if not is_animal(n)], [n for n in kept if is_animal(n)]


def _animal_brief(names: list[str], analysis: dict | None = None) -> str:
    """动物/灵兽的**精简**提法，用于 [场景] 段。

    只取角色卡的**第一个分句**（≤30 字）—— 卡里那串并列分句正是被误当成多个主体的
    嫌疑来源，所以这里刻意不整段搬。C 组实验里场景只说了一句「身旁黑牛盘腿而坐」
    就出了正确的 1 头牛。
    """
    card = (analysis or {}).get("characters") or {}
    parts = []
    for name in names:
        desc = (card.get(name) or "").split("，")[0].split("。")[0].strip()[:30]
        parts.append(f"{name}（{desc}）" if desc else name)
    return "、".join(parts)


def _format_characters(seg: dict, analysis: dict | None = None) -> str:
    """[角色锚] 只列**人物**（动物/灵兽见 _animal_brief，走 [场景] 段）。

    有角色特征卡就用『名字(特征)』锁定描述，没有则只列名字。
    **只列本镜真正出现的人物**（见 _mentioned_characters）。
    """
    names, _ = _split_characters(seg, analysis)
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


# [镜头] 里凡是**声明主体人数**的措辞，一律删掉。
#
# ★ 实测（2026-09-17 第一章 shot1）：这类措辞会被 agnès 当成硬构图指令。
#   同一镜，只改这句话出三组图：
#     角色锚含陈浔+大黑牛        → 每张都是「1 少年 + 2 头牛」
#     角色锚只留陈浔             → 每张都是「**2 个一模一样的少年**」（凑人数）
#     角色锚只留陈浔 + 删掉这句   → 每张都是「1 个少年，没有牛」✓ 正确
#   人数已经由 [角色锚]（按镜裁剪）和 [主体动作] 表达过了，镜头段再说一遍只会打架。
_SUBJECT_COUNT_RE = re.compile(
    r"(?:[一二两双]\s*人并排|双人并排|一人一牛|一牛一人|人牛并排|双人|二人|两人)"
)

# 与末尾红线「严禁面部特写」冲突的措辞：镜头段命令去拍脸，红线又不许拍脸 —— 模型会听前半句。
# 实测（2026-09-17 画布 40 第 2 段）：镜头写「特写推近，从火焰快速推至少年惊愕面部」，
# 出图就是整屏一张大脸 + 竖构图，直接踩红线。红线在末尾、权重高，所以删镜头里的拍脸指令。
_FACE_SEGMENT_RE = re.compile(r"[^，；]*(?:面部|脸部|面孔|五官|表情|脸)[^，；]*")

# 景别里的「特写」档：有人物的镜头里会直接推到脸上（见 _sanitize_camera 的注释）
_CLOSEUP_RE = re.compile(r"(?:大特写|近景特写|特写|怼脸)")


def _sanitize_camera(camera: str, has_human: bool = True) -> str:
    """清理 [镜头] 段：删主体人数措辞、删拍脸指令、把「特写」降档。

    ★ 「特写」这一档本身就会把画面推成一张脸（单人物镜头必然落在脸上）。
    实测（2026-09-17 画布 40 第 2 段 A/B）：只删「…面部」不动「特写」，
    出图依旧是整屏一张脸 —— 所以有人的镜头里把景别降一档（特写 → 中景）。
    """
    s = _SUBJECT_COUNT_RE.sub("", camera or "")
    # 删除含"脸"的整个分句（「从火焰快速推至少年惊愕面部」这种，删掉比留着安全：
    # 红线的约束是硬要求，镜头段少一个运镜描述不影响成片）
    if s:
        segs = [x for x in s.split("，") if not _FACE_SEGMENT_RE.fullmatch(x.strip("， "))]
        s = "，".join(x for x in segs if x.strip())
    # 有人物时「特写/大特写」一律降到中景：红线禁面部特写，而人物特写几乎必然拍到脸
    if has_human and s:
        s = _CLOSEUP_RE.sub("中景", s)
    s = re.sub(r"[，、]{2,}", "，", s).strip("，、 ")
    return s


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
    humans, animals = _split_characters(seg, analysis)
    # 动物/灵兽不进 [角色锚]（一旦进去模型就画两头），改用 [场景] 里一句短的带出
    if animals:
        # 场景里已经点名过的就不再追加描述：同一角色在场景段里出现两次会不会又诱发复制，
        # 没验证过；沿用 C 组（实测「1 人 1 牛」）的形状最稳。
        todo = [n for n in animals if not any(a and a in scene for a in _char_aliases(n))]
        if todo:
            brief = _animal_brief(todo, analysis)
            # 用「，」拼接：`；` 是本文件的分段块分隔符，用它会多切出一段、也破坏提示词结构。
            scene = f"{scene}，{brief}" if scene else brief
    # 有人物的镜头才降「特写」档：纯景物的特写（米袋、斧头）不违反红线
    camera = _ensure_camera_terms(_sanitize_camera(seg.get("camera", ""), bool(humans)))
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
