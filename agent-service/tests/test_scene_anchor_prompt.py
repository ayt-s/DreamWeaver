"""场景锚图只画环境：剥主体子句 + 空景硬约束（2026-09-19）。

背景（实测，不是推测）：首帧「多画主体」的真因是**场景锚图里本来就有主体** ——
`废墟` 锚图画着「一人一牛」，于是以它当参考图的废墟镜「带锚 人多 4/5 vs 不带 0/10」；
而 `山洞` 锚图是空景 → 该镜从不多画、也不丢主体。
提示词里本来就有「空场景无角色」，但**抽象约束挡不住具体描述**：
场景描述原文写着「一人一牛跪坐门外」，模型就照画 ⇒ 必须在喂进提示词前把主体子句剥掉。
"""

from app.controller.novel_anchors_api import _SCENE_PROMPT_TEMPLATE
from app.utils.prompting import strip_subject_clauses

RUINS = ("山村茅屋废墟，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，"
         "一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助")


def test_scene_desc_subject_clauses_stripped_environment_kept():
    got = strip_subject_clauses(RUINS, ("陈浔", "大黑牛"))
    assert "一人一牛" not in got and "跪坐门外" not in got
    for env in ("山村茅屋废墟", "黄昏", "余烬未熄", "黑烟仍在上升", "焦黑的木梁倒在地上", "氛围凄凉无助"):
        assert env in got, env


def test_scene_desc_generic_subject_and_character_name_stripped():
    got = strip_subject_clauses("清晨，阳光斜照，少年叼着狗尾草躺坐，微风拂过万木倾伏", ("陈浔",))
    assert "少年" not in got
    assert "阳光斜照" in got and "微风拂过万木倾伏" in got
    got2 = strip_subject_clauses("村外山洞，日间，陈浔站在洞口，洞口透进自然光", ("陈浔",))
    assert "陈浔" not in got2 and "洞口透进自然光" in got2


def test_scene_desc_location_name_with_villagers_survives():
    """★ 反例保护：「村民宴会场地」是地点名，不能被泛称词规则误删。"""
    got = strip_subject_clauses("村民宴会场地，夜晚，红布覆盖长桌，火把照亮夜空", ())
    assert "村民宴会场地" in got and "火把照亮夜空" in got


def test_scene_desc_falls_back_instead_of_becoming_empty():
    """兜底：剥完不像样的描述就原文返回 —— 绝不交出空描述（场景信息全丢更糟）。"""
    assert strip_subject_clauses("陈浔跪坐门外", ("陈浔",)) == "陈浔跪坐门外"
    assert strip_subject_clauses("", ()) == ""


def test_scene_prompt_template_carries_empty_scene_constraint():
    """模板里必须有具体的「无人/无动物」约束（第二道防线）。"""
    p = _SCENE_PROMPT_TEMPLATE.format(style="写实", scene_desc="山村茅屋废墟，黄昏")
    assert "空场景无角色" in p
    assert "不得出现任何人" in p and "动物" in p and "人影" in p
