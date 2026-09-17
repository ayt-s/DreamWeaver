"""[角色锚] 必须按镜裁剪。

背景（2026-09-17 第一章实测）：`[角色锚]` 里列了谁，agnès 就把谁都画出来 ——
而且「大黑牛」会被画成**两头**，6 镜 18 张图无一例外，连 [主体动作] 明写
「一人一牛」的那镜也是两头。根因是 storyboarder 给每镜都分配了全部角色，
角色锚照单全收 = 每镜都在点单。
"""
from app.novel.composer import (
    _mentioned_characters,
    compose_image_prompt,
    compose_video_prompt,
)

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
    # ★ 「中近景」属于近距离档 → 有人物的镜头里一起压到中景（P0-3）。
    #   此处断言的是**新契约**：既不能被拆成「中中景」，也不能原样留下。
    assert "中近景" not in camera
    assert "中景" in camera
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


def test_镜头里的拍脸指令会被删掉_红线优先():
    """★ 实测：镜头写「特写推近，从火焰快速推至少年惊愕面部」时，出图是整屏大脸 + 竖构图，
    直接踩「严禁面部特写」红线 —— 模型听了镜头段、没听末尾红线。"""
    p = compose_image_prompt(
        {**seg("陈浔突然闻到焦味"), "camera": "特写推近，从火焰快速推至少年惊愕面部，背景烟雾缭绕"},
        "3D 写实国漫",
        ANALYSIS,
    )
    camera = p.split("[镜头]")[1].split("；")[0]
    assert "面部" not in camera and "脸" not in camera
    # ★ 光删「面部」不够：只留「特写推近」实测照样是整屏一张脸 → 景别必须一起降档
    assert "特写" not in camera
    assert "中景推近" in camera and "背景烟雾缭绕" in camera


def test_有人物的特写一律降成中景():
    """红线禁面部特写，而单人物镜头里的「特写」几乎必然落在脸上。"""
    p = compose_image_prompt({**seg("陈浔握紧开山斧"), "camera": "大特写"}, "3D 写实国漫", ANALYSIS)
    camera = p.split("[镜头]")[1].split("；")[0]
    assert "特写" not in camera and "中景" in camera


def test_纯景物特写不降档():
    """没有人物时「米袋特写」不违反红线，别乱改。"""
    p = compose_image_prompt(
        {**seg("一枚米袋落在门口", characters=()), "camera": "米袋特写"},
        "3D 写实国漫",
        ANALYSIS,
    )
    assert "特写" in p.split("[镜头]")[1].split("；")[0]


def test_脸部与面部表情的分句都删():
    p = compose_image_prompt(
        {**seg("陈浔握紧开山斧"), "camera": "仰拍缓推，从陈浔脸部推至半身，突出咬牙握斧的面部表情，人物偏左居画面三分线"},
        "3D 写实国漫",
        ANALYSIS,
    )
    camera = p.split("[镜头]")[1].split("；")[0]
    assert "脸" not in camera and "表情" not in camera
    assert "仰拍缓推" in camera and "人物偏左居画面三分线" in camera


def test_不含脸部词的镜头原样保留():
    cam = "中景缓慢横移，从远景山林推向少年，主体居中，前景有野草虚化"
    p = compose_image_prompt({**seg("陈浔躺坐山坡"), "camera": cam}, "3D 写实国漫", ANALYSIS)
    assert p.split("[镜头]")[1].split("；")[0].strip() == cam


def test_只剩拍脸指令时回落到默认镜头():
    p = compose_image_prompt({**seg("陈浔握斧"), "camera": "面部大特写"}, "3D 写实国漫", ANALYSIS)
    assert "中景固定" in p


def test_结构化字段认得出关键词表漏掉的灵兽():
    """★ A 的意义：关键词表永远认不出「饕餮」「麒麟」这类名字，结构化字段能。"""
    analysis = {
        "characters": {"李寻": "少年，短打", "饕餮": "巨口獠牙的上古凶兽"},
        "animal_characters": ["饕餮"],
    }
    p = compose_image_prompt(
        seg("李寻与饕餮对峙", characters=("李寻", "饕餮")), "3D 写实国漫", analysis
    )
    assert "饕餮" not in _anchor(p)
    assert "饕餮" in p.split("[场景]")[1].split("；")[0]


def test_结构化字段说不是动物就绝不猜():
    """「黑牛」是壮汉：分析器明确没列他 → 必须留在角色锚（关键词表会误伤）。"""
    analysis = {"characters": {"黑牛": "中年壮汉，络腮胡，戴斗笠"}, "animal_characters": []}
    p = compose_image_prompt(seg("黑牛握刀而立", characters=("黑牛",)), "3D 写实国漫", analysis)
    assert "黑牛" in _anchor(p)


def test_没有结构化字段时退回关键词猜():
    """老分析结果（没这个字段）仍要能用 —— 此时才走词表。"""
    old = {"characters": {"大黑牛": "通体漆黑的灵兽牛，左角已断"}}
    p = compose_image_prompt(seg("黑牛反刍保下大米", characters=("大黑牛",)), "3D 写实国漫", old)
    assert "大黑牛" not in _anchor(p)


def test_字段被camel化也能读到():
    analysis = {"characters": {"饕餮": "凶兽"}, "animalCharacters": ["饕餮"]}
    assert "饕餮" not in _anchor(
        compose_image_prompt(seg("饕餮扑来", characters=("饕餮",)), "3D 写实国漫", analysis)
    )


def test_六段结构与红线不受影响():
    prompt = compose_image_prompt(seg("陈浔握紧开山斧"), "3D 写实国漫", ANALYSIS)
    for block in ("[角色锚]", "[主体动作]", "[场景]", "[镜头]", "[风格]"):
        assert block in prompt
    assert "严禁面部特写" in prompt
    assert "无文字乱码" in prompt


# === 裁剪口径的收窄修复（2026-09-17 复查发现） ===

VIDEO_ANALYSIS = {
    "characters": {
        "陈浔": "十七八岁少年，黑色短发，粗布短褂",
        "王二": "四十岁壮汉，络腮胡，戴斗笠",
    }
}


def test_名字只写在镜头段的角色也必须锁定长相():
    """★ 收窄修复：镜头段会点名角色（「全景，王二站在门口」）。

    只扫 plot+scene 时，王二会被静默踢出角色锚 —— 而角色锚是面部一致性的唯一来源，
    「本镜确实出现却没锁定长相」比多画一个更难发现。
    """
    p = compose_image_prompt(
        {
            "plot": "陈浔回头，看见那壮汉已站在门口",
            "scene": "客栈大堂，夜晚，烛火昏黄",
            "camera": "全景，王二站在门口",
            "characters": ["陈浔", "王二"],
            "mood": "暗流",
        },
        "国风写实",
        VIDEO_ANALYSIS,
    )
    anchor = _anchor(p)
    assert "陈浔" in anchor
    assert "王二" in anchor, "名字出现在镜头段的角色不能被踢出角色锚"


def test_全篇都没提到才该踢出角色锚():
    """另一端：名字在任何字段都没出现 → 仍然裁掉（这是 ① 的本意）。"""
    p = compose_image_prompt(seg("陈浔独自握紧开山斧"), "3D 写实国漫", ANALYSIS)
    assert "陈浔" in _anchor(p)
    assert "大黑牛" not in _anchor(p)


def test_视频提示词的角色锚与图片提示词一致():
    """拼视频提示词时 seg 里已经有 imagePrompt 了。

    如果裁剪把 imagePrompt 也扫进来，第二次拼装会「自证命中」→ 角色锚退回全给，
    图片与视频两段提示词的口径就不一致了。
    """
    s = seg("陈浔独自握紧开山斧")
    img = compose_image_prompt(s, "3D 写实国漫", ANALYSIS)
    s["imagePrompt"] = img  # 编排器就是这样先写回再拼视频提示词的
    vid = compose_video_prompt(s, "3D 写实国漫", ANALYSIS)
    assert _anchor(vid) == _anchor(img)
    assert "大黑牛" not in _anchor(vid)


# === 造型锁定（P0-2，2026-09-17 复查后新增） ===

COSTUME_ANALYSIS = {
    "characters": {
        "陈浔": "十七八岁少年，黑色短发，常穿粗布短衫与靛蓝长裤，腰间别一柄开山斧",
        "阿禾": "十五岁少女，扎双髻，身着青布衣裙，手腕系红绳",
    }
}


def test_角色卡里的造型分句要重复到条目末尾():
    """★ 实测：同一张卡，前一批出「土黄短打+补丁裤」、后一批出「绿袍束发」——

    卡本身一直在锚里，所以「再说一遍全卡」没用；把造型分句重复到**条目末尾**。
    """
    p = compose_image_prompt(
        seg("陈浔握紧开山斧", characters=("陈浔",)), "3D 写实国漫", COSTUME_ANALYSIS
    )
    anchor = _anchor(p)
    assert "全片服装发型保持一致" in anchor, "造型分句必须被重复一次"
    assert "粗布短衫" in anchor


def test_造型短语不许抠成长篇大论():
    """只抠分句、且截断到 24 字：整卡重复会放大「并列分句被当成多个主体」那个问题。"""
    long_card = {
        "characters": {
            "陈浔": "十七八岁少年，" + "，".join(f"细节{i}" for i in range(12)) + "，常穿粗布短衫与靛蓝长裤，腰间别斧"
        }
    }
    p = compose_image_prompt(seg("陈浔握斧", characters=("陈浔",)), "3D 写实国漫", long_card)
    tail = _anchor(p).split("全片服装发型保持一致：")[1].split("）")[0]
    assert len(tail) <= 24


def test_卡里没有造型词就不编():
    """抠不到造型分句时**不许编**一句「服装一致」出来 —— 提示词里每一句都要有出处。"""
    bare = {"characters": {"陈浔": "十七八岁少年，眉目清秀，眼神慵懒"}}
    p = compose_image_prompt(seg("陈浔躺坐山坡", characters=("陈浔",)), "3D 写实国漫", bare)
    assert "全片服装发型保持一致" not in _anchor(p)

    # 对照：卡里有「短发」这类发型词时应当命中（线索引词表含发型，不只是衣服）
    with_hair = {"characters": {"陈浔": "十七八岁少年，黑色短发，眼神慵懒"}}
    p2 = compose_image_prompt(seg("陈浔躺坐山坡", characters=("陈浔",)), "3D 写实国漫", with_hair)
    assert "全片服装发型保持一致" in _anchor(p2)


def test_末尾红线含造型一致约束():
    """红线在末尾、权重最高，是唯一能同时约束所有镜头的落点。"""
    p = compose_image_prompt(seg("陈浔握斧"), "3D 写实国漫", COSTUME_ANALYSIS)
    assert "同一角色在各镜头中的服装与发型必须完全一致" in p
    assert "不得换装" in p


def test_造型重复不影响六段结构与动物分流():
    p = compose_image_prompt(
        seg("陈浔与断角黑牛对峙", scene="山坡，黄昏", characters=("陈浔", "大黑牛")),
        "3D 写实国漫",
        {**COSTUME_ANALYSIS, "characters": {**COSTUME_ANALYSIS["characters"],
                                            "大黑牛": "通体漆黑的灵兽牛，左角齐根断去"}},
    )
    for block in ("[角色锚]", "[主体动作]", "[场景]", "[镜头]", "[风格]"):
        assert block in p
    assert "大黑牛" not in _anchor(p), "动物仍不进角色锚"


# === 关键道具接线（P1-5：props 此前是死字段） ===

PROP_ANALYSIS = {
    "characters": {"陈浔": "十七八岁少年，常穿粗布短衫"},
    "props": ["锈迹斑斑的开山斧（斧柄缠麻绳）", "鼓鼓的米袋（粗麻布）"],
}


def test_本镜提到的道具补进场景段():
    """道具是画面信息；此前 analyzer 产出它、却没有任何消费方（白花 token）。"""
    p = compose_image_prompt(
        seg("陈浔握紧开山斧劈向枯木", scene="山林，黄昏"), "3D 写实国漫", PROP_ANALYSIS
    )
    scene = p.split("[场景]")[1].split("；")[0]
    assert "开山斧" in scene
    assert "米袋" not in scene, "本镜没提的道具不许整片塞进来"


def test_道具匹配是字面后缀_不做同义_已知局限():
    """**已知局限**：道具匹配是字面后缀匹配，「斧头」不会命中「开山斧」。

    钉住它是为了说明这是有意为之：再强的匹配要靠 LLM，不该塞进确定性拼装。
    真觉得需要同义匹配时，改动点在这里（_prop_aliases），别偷偷加词表。
    """
    p = compose_image_prompt(
        seg("陈浔扛着斧头走过山路", scene="山路，清晨"), "3D 写实国漫", PROP_ANALYSIS
    )
    assert "开山斧" not in p.split("[场景]")[1].split("；")[0]

    # 对照：写成道具表里的核心名词就能命中（这才是匹配口径）
    p2 = compose_image_prompt(
        seg("陈浔扛着开山斧走过山路", scene="山路，清晨"), "3D 写实国漫", PROP_ANALYSIS
    )
    assert "开山斧" in p2.split("[场景]")[1].split("；")[0]


def test_没有任何道具命中时场景段不变():
    p = compose_image_prompt(seg("陈浔躺坐山坡"), "3D 写实国漫", PROP_ANALYSIS)
    scene = p.split("[场景]")[1].split("；")[0]
    assert "开山斧" not in scene and "米袋" not in scene
    assert scene.strip() == "山洞洞口，黄昏时分"


def test_props形状异常不炸():
    bad = {"characters": {"陈浔": "少年"}, "props": "开山斧"}
    p = compose_image_prompt(seg("陈浔握斧"), "3D 写实国漫", bad)
    assert "[场景]" in p


# === 近景降档（P0-3 保守档）与 animal 告警（P1-6） ===


def test_有人物的近景也降一档():
    """★ ch1d 对照实测：只降「特写」档仍有 1/3 出整屏大脸 —— 单人物镜头落到「近景」时

    必然以脸为主体。代价是镜头语言变单调（审美取舍，改动点就是 _CLOSEUP_RE）。
    """
    p = compose_image_prompt({**seg("陈浔握紧开山斧"), "camera": "近景推近"}, "3D 写实国漫", ANALYSIS)
    camera = p.split("[镜头]")[1].split("；")[0]
    assert "近景" not in camera
    assert "中景" in camera


def test_纯景物的近景不降档():
    """没有人物就没有拍脸风险：米袋/斧头的近景是有效镜头语言，不该一起压平。"""
    p = compose_image_prompt(
        {**seg("米袋近景", characters=()), "camera": "近景"}, "3D 写实国漫", ANALYSIS
    )
    assert "近景" in p.split("[镜头]")[1].split("；")[0]


def test_analyzer说没有动物但关键词判出动物时记日志(caplog):
    """P1-6：**只留痕、不改行为** —— 空数组权威是为了保护「黑牛是壮汉」那一端。"""
    caplog.set_level("WARNING", logger="app.novel.composer")
    analysis = {
        "characters": {"大黑牛": "通体漆黑的灵兽牛，左角齐根断去"},
        "animal_characters": [],
    }
    p = compose_image_prompt(
        seg("黑牛反刍保下大米", characters=("大黑牛",)), "3D 写实国漫", analysis
    )
    # 行为不变：仍按模型的答案执行（牛留在角色锚里）
    assert "大黑牛" in _anchor(p)
    assert "关键词判出动物" in caplog.text, "应当留下可检索的日志"
    assert "大黑牛" in caplog.text, "日志里要能看出是哪个角色"


def test_字段缺失走关键词_不记这种日志(caplog):
    """老数据（没这个字段）本来就该走关键词兜底，不该刷告警。"""
    caplog.set_level("WARNING", logger="app.novel.composer")
    old = {"characters": {"大黑牛": "通体漆黑的灵兽牛，左角齐根断去"}}
    compose_image_prompt(seg("黑牛反刍保下大米", characters=("大黑牛",)), "3D 写实国漫", old)
    assert not [r for r in caplog.records if "关键词判出动物" in str(r.msg)]
