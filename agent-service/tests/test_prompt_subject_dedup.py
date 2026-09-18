"""主体去重：场景段剥主体子句 + 镜头段动物提名去重（2026-09-18）。

背景（实测，非推测）：用户画布 40 的 6 张真实首帧里 3 张有多余主体 ——
  · #5 画面 2 个人：场景锚原文写「一人一牛跪坐门外」，而 [角色锚] 又列了陈浔；
  · #6 画面 2 头牛：场景段动物简述 + [镜头]「绕少年与黑牛缓慢旋转」各提名一次。
机制：**同一主体在提示词里被指定两次，模型就画两份**（对照 #3：只提名一次 → 只出一头）。
"""

from app.novel.composer import (
    _drop_animal_mentions,
    _strip_subject_clauses,
    compose_image_prompt,
)

ANALYSIS = {
    "characters": {
        "陈浔": "17-18岁少年，黑色短发，穿粗布短褂和靛蓝长裤",
        "大黑牛": "通体漆黑的灵兽牛",
        "小黑子": "小黑牛",
    },
    "animal_characters": ["大黑牛", "小黑子"],
}


def test_scene_subject_count_clause_is_stripped():
    """场景锚原文里的「一人一牛」这类主体短语必须去掉，环境子句一个不少。"""
    raw = "山村茅屋废墟，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助"
    got = _strip_subject_clauses(raw, ANALYSIS)
    assert "一人一牛" not in got
    assert "跪坐门外" not in got            # 动作由 [主体动作] 承担，场景里不留
    for env in ("山村茅屋废墟", "黄昏", "余烬未熄", "黑烟仍在上升", "焦黑的木梁倒在地上", "氛围凄凉无助"):
        assert env in got, env


def test_scene_character_and_generic_subject_clauses_are_stripped():
    """点名角色的子句、泛称主体的子句都算主体子句。"""
    got = _strip_subject_clauses("小山村山坡，清晨，阳光斜照，少年叼着狗尾草躺坐，微风拂过万木倾伏", ANALYSIS)
    assert "少年" not in got
    assert "小山村山坡" in got and "阳光斜照" in got and "微风拂过万木倾伏" in got
    got2 = _strip_subject_clauses("村外山洞，日间，大黑牛（通体漆黑的灵兽牛），洞口透进自然光", ANALYSIS)
    assert "大黑牛" not in got2 and "洞口透进自然光" in got2


def test_scene_location_name_containing_villagers_is_preserved():
    """★ 反例保护：「村民宴会场地」是地点名，不能被泛称词规则误删。"""
    got = _strip_subject_clauses("村民宴会场地，夜晚，红布覆盖长桌，火把照亮夜空", ANALYSIS)
    assert "村民宴会场地" in got and "火把照亮夜空" in got


def test_scene_strip_falls_back_when_result_too_short():
    """兜底：剥完没有像样的环境就原文返回 —— 绝不交出空场景。"""
    assert _strip_subject_clauses("陈浔跪坐门外", ANALYSIS) == "陈浔跪坐门外"
    assert _strip_subject_clauses("", ANALYSIS) == ""


def test_camera_animal_mention_dropped_when_scene_already_has_it():
    """同一主体只提名一次：场景已简述动物 → 镜头里那次去掉（#6 两头牛的成因）。"""
    scene = "村外山洞，日间，洞内灰褐色石壁，大黑牛（通体漆黑的灵兽牛）"
    got = _drop_animal_mentions("中景环绕，绕少年与黑牛缓慢旋转，展现山洞环境", ["大黑牛"], scene)
    assert "黑牛" not in got
    assert got.startswith("中景环绕，绕少年缓慢旋转")     # 连词一并吃掉，句子仍通顺


def test_camera_animal_mention_kept_when_scene_has_no_animal():
    """场景里没提动物 → 镜头这次是**唯一**提名，必须保留。"""
    cam = "中景环绕，绕黑牛缓慢旋转"
    assert _drop_animal_mentions(cam, ["大黑牛"], "村外山洞，日间") == cam


def test_camera_drop_falls_back_instead_of_emptying_the_shot():
    """兜底：删完太短就原文返回 —— 镜头信息没了比多画一个主体更糟。"""
    assert _drop_animal_mentions("黑牛", ["大黑牛"], "场景里写着大黑牛") == "黑牛"


def test_end_to_end_prompt_has_single_subject_mention():
    """端到端：#6 那一段合成后，动物在整条提示词里只被提名一次。"""
    seg = {
        "characters": ["陈浔", "大黑牛"],
        "scene": "村外山洞，日间，洞内灰褐色石壁，地面铺满干草，角落堆放干柴和简陋木器，洞口透进自然光，清冷中透着安稳",
        "camera": "中景环绕，绕少年与黑牛缓慢旋转，展现山洞环境与进食动作",
        "subject": "村外山洞内",
        "mood": "狡黠满足",
    }
    prompt = compose_image_prompt(seg, "3D 写实国漫、虚幻 5 渲染、颗粒感", ANALYSIS)
    camera = prompt.split("[镜头]")[1].split("[风格]")[0]
    assert "黑牛" not in camera                      # 镜头段不再提名动物
    assert prompt.count("大黑牛") == 1               # 全篇只提名一次（场景段那句简述）
    assert "严禁复制主体" in prompt                   # 末尾兜底红线在了
