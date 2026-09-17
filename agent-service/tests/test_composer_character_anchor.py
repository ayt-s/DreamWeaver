"""[角色锚] 必须按镜裁剪。

背景（2026-09-17 第一章实测）：`[角色锚]` 里列了谁，agnès 就把谁都画出来 ——
而且「大黑牛」会被画成**两头**，6 镜 18 张图无一例外，连 [主体动作] 明写
「一人一牛」的那镜也是两头。根因是 storyboarder 给每镜都分配了全部角色，
角色锚照单全收 = 每镜都在点单。
"""
from app.novel.composer import _mentioned_characters, compose_image_prompt

ANALYSIS = {
    "characters": {
        "陈浔": "十七八岁少年，黑色短发，粗布短褂与靛蓝长裤，腰间插着开山斧",
        "大黑牛": "成年玄色水牛，左角齐根断去、断面泛白",
    }
}


def seg(plot, scene="山洞洞口，黄昏时分", characters=("陈浔", "大黑牛")):
    return {
        "plot": plot,
        "scene": scene,
        "camera": "中景固定",
        "characters": list(characters),
        "mood": "憧憬神气",
    }


def _anchor(prompt: str) -> str:
    """取出 [角色锚] 段。"""
    return prompt.split("[角色锚]")[1].split("；")[0]


def test_只提到主角的镜头不该再点上一头牛():
    """核心回归：动作里只有陈浔，角色锚就不能带大黑牛。"""
    prompt = compose_image_prompt(seg("少年陈浔嘴叼狗尾草躺坐在青翠小山坡上"), "3D 写实国漫", ANALYSIS)
    anchor = _anchor(prompt)
    assert "陈浔" in anchor
    assert "大黑牛" not in anchor


def test_简称黑牛也要能命中大黑牛():
    """storyboarder 不会总写全名（「黑牛反刍保下些许大米」）——命中后走场景段。"""
    p = compose_image_prompt(seg("黑牛反刍保下些许大米"), "3D 写实国漫", ANALYSIS)
    assert "大黑牛" not in _anchor(p)
    assert "大黑牛" in p.split("[场景]")[1].split("；")[0]


def test_动物不进角色锚而是走场景段():
    """★ 实测：大黑牛只要出现在 [角色锚] 里，每张都会画出两头牛（A/B/C 三组对照）。"""
    p = compose_image_prompt(
        seg("少年陈浔嘴叼狗尾草躺坐山坡", scene="小山村山坡，清晨，阳光斜照，身旁黑牛盘腿而坐"),
        "3D 写实国漫",
        ANALYSIS,
    )
    anchor = _anchor(p)
    assert "陈浔" in anchor
    assert "大黑牛" not in anchor, "动物不该进角色锚（进就会被画成两头）"
    scene = p.split("[场景]")[1].split("；")[0]
    assert "黑牛" in scene, "但它仍然要在场（场景段已点名）"
    # 只带角色卡的第一个分句：那串并列分句正是被当成多个主体的嫌疑来源
    assert "右角完整尖锐" not in scene


def test_卡里出现动物词不会把人物误判成动物():
    from app.novel.composer import _is_animal

    assert _is_animal("大黑牛") is True
    assert _is_animal("陈浔") is False
    assert _is_animal("小黑子") is False


def test_纯动物镜头也不会把动物塞回角色锚():
    p = compose_image_prompt(
        seg("黑牛独自反刍", scene="山洞内，黄昏", characters=("大黑牛",)),
        "3D 写实国漫",
        ANALYSIS,
    )
    assert "大黑牛" not in _anchor(p)
    assert "大黑牛" in p.split("[场景]")[1].split("；")[0]


def test_一个名字都没匹配到时退回全给_不劣化():
    """匹配不到 ≠ 这镜没角色（可能用了代称）——宁可多画，不能漏角色。"""
    assert _mentioned_characters(seg("风吹过草坡，四下寂静")) == ["陈浔", "大黑牛"]


def test_两字名字按全名匹配():
    assert _mentioned_characters(seg("陈浔握紧开山斧", characters=("陈浔",))) == ["陈浔"]


def test_场景里提到的角色也要保留():
    """角色可能只出现在 [场景] 描述里（「陈浔与大黑牛并排坐」）。"""
    hit = _mentioned_characters(seg("一人一牛席地而坐", scene="山洞洞口，陈浔与大黑牛并排"))
    assert "大黑牛" in hit and "陈浔" in hit


def test_镜头里的人数措辞会被删掉():
    """★ 实测：agnès 把「双人并排」当硬构图指令 —— 角色锚只列陈浔时，它会凑出两个陈浔。"""
    p = compose_image_prompt(
        {**seg("少年陈浔嘴叼狗尾草躺坐在小山坡上"), "camera": "广角长镜头缓慢横移，中近景双人并排，人物居画面三分线"},
        "3D 写实国漫",
        ANALYSIS,
    )
    camera = p.split("[镜头]")[1].split("；")[0]
    assert "双人" not in camera and "并排" not in camera
    # 专业镜头术语不能连带被删
    assert "广角长镜头缓慢横移" in camera
    assert "中近景" in camera
    assert "三分线" in camera


def test_人数说法被删空时回落到默认镜头():
    p = compose_image_prompt({**seg("陈浔握紧开山斧"), "camera": "双人并排"}, "3D 写实国漫", ANALYSIS)
    assert "中景固定" in p


def test_一人一牛这类说法也删():
    p = compose_image_prompt(
        {**seg("陈浔握紧开山斧"), "camera": "广角长镜头从火场全景缓慢拉远，中景一人一牛跪在屋前背影"},
        "3D 写实国漫",
        ANALYSIS,
    )
    assert "一人一牛" not in p
    assert "广角长镜头从火场全景缓慢拉远" in p


def test_场景已点名过的动物不再重复追加():
    """C 组形状（场景自然提到黑牛、角色锚只留少年）实测就是「1 人 1 牛」，
    所以场景点名过就别再画蛇添足重复一遍。"""
    p = compose_image_prompt(
        seg("少年陈浔躺坐山坡", scene="小山村山坡，清晨，身旁黑牛盘腿而坐"),
        "3D 写实国漫",
        ANALYSIS,
    )
    scene = p.split("[场景]")[1].split("；")[0]
    assert scene.count("黑牛") == 1


def test_六段结构与红线不受影响():
    prompt = compose_image_prompt(seg("陈浔握紧开山斧"), "3D 写实国漫", ANALYSIS)
    for block in ("[角色锚]", "[主体动作]", "[场景]", "[镜头]", "[风格]"):
        assert block in prompt
    assert "严禁面部特写" in prompt
    assert "无文字乱码" in prompt
