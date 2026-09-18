"""存量画布提示词重算（`POST /v1/novel/recompose-prompts`）的规则与结构护栏。

两份提示词是**真实画布**的数据（原样嵌入，不是人工抄的）：
- `REAL_OLD_PROMPT` = 画布 39 v6 `img0`（**存量老形状**：动物「大黑牛」还在 `[角色锚]` 里，
  且它的角色卡里含 `、`（「左角齐根断去、断面粗糙泛白」））
- `REAL_CURRENT_PROMPT` = 画布 40 v16 `img3`（**当前形状**：动物已在 `[场景]` 里带出）

判据来源：`app/novel/composer.py`（`_is_animal` / `_animal_names_from_analysis` /
`_char_aliases` / `_sanitize_camera`）——本模块 import 的就是这几个函数本身。
"""

import json

from fastapi.testclient import TestClient

from app.main import app
from app.novel.prompt_recompose import recompose_prompt, recompose_prompts

REAL_OLD_PROMPT = '[角色锚] 陈浔（17-18岁少年，男性，身形清瘦中等，黑色短发略显凌乱，眉目清秀但常带促狭笑意，肤色偏黄，常穿粗布短褂和靛蓝长裤，腰间系一条旧布带，脚踩草鞋，举止懒洋洋却透着机灵，嘴角常叼着狗尾草）、大黑牛（成年玄色水牛体型，通体漆黑毛皮油亮，左角齐根断去、断面粗糙泛白，右角完整呈弯月状，能以后腿坐立、前蹄收起，背脊挺直如人坐，铜铃大眼泛着精光，鼻孔宽大喷气有力）；[主体动作] 少年陈浔嘴叼狗尾草躺坐在青翠小山坡上；[场景] 青翠小山坡，白日，微风拂过万木倾伏，阳光洒落草坡，远处层峦叠嶂；[镜头] 广角长镜头缓慢横移，中近景双人并排，人物居画面三分线，前景有青草装饰；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪闲散窃喜。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。'

REAL_CURRENT_PROMPT = '[角色锚] 陈浔；[主体动作] 陈浔突然闻到焦味；[场景] 山村茅草屋，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助，大黑牛（通体漆黑的灵兽牛）；[镜头] 中景推近，背景烟雾缭绕；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪惊慌绝望。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。'


def _parts(prompt: str) -> list[str]:
    return prompt.split("；")


def test_real_old_canvas_moves_animal_out_of_anchor():
    """存量老画布：动物必须从 [角色锚] 移出、改到 [场景] 里带一句。"""
    r = recompose_prompt(REAL_OLD_PROMPT, None)

    assert r["changed"] is True
    anchor = _parts(r["prompt"])[0]
    assert anchor.startswith("[角色锚] 陈浔"), f"锚点应当只剩人物：{anchor[:60]}"
    assert "大黑牛" not in anchor
    assert "断面粗糙泛白" not in anchor, "角色卡里含 `、`，被切成碎片后必须合回上一项"

    scene = _parts(r["prompt"])[2]
    assert scene.startswith("[场景] ")
    assert "大黑牛（成年玄色水牛体型）" in scene, "动物改成在场景里带一句短的"

    # 未触碰的段落必须**逐字**保留（[镜头] 之外一个字节都不动）
    assert len(_parts(r["prompt"])) == len(_parts(REAL_OLD_PROMPT))
    for i, (before, after) in enumerate(zip(_parts(REAL_OLD_PROMPT), _parts(r["prompt"]))):
        if i in (0, 2, 3):
            continue
        assert before == after, f"第 {i} 段被无端改动"


def test_real_current_canvas_is_idempotent():
    """当前形状的提示词再重算一次必须**逐字不变**（否则每次点都会抖动）。"""
    first = recompose_prompt(REAL_CURRENT_PROMPT, None)
    assert first["changed"] is False, first["reasons"]
    assert first["prompt"] == REAL_CURRENT_PROMPT

    twice = recompose_prompts(None, [{"id": "x", "prompt": REAL_OLD_PROMPT}])
    again = recompose_prompts(
        None, [{"id": "x", "prompt": twice["nodes"][0]["prompt"]}]
    )
    assert again["changed_count"] == 0, "重算一次之后再重算必须无事可做"


def test_bad_structure_is_skipped_verbatim():
    """★ 安全约束：结构不对的节点跳过不硬改（画布 7 的 imageNode 提示词就是空的）。"""
    for bad in ("", "随手写的一句话", "[角色锚] 陈浔；[场景] 山洞", "文本节点内容，没有分段"):
        r = recompose_prompt(bad, None)
        assert r["changed"] is False
        assert r["skipped"] is True
        assert r["prompt"] == bad, "跳过必须逐字返回原文"
        assert r["reasons"] and "跳过" in r["reasons"][0]


def test_analyzer_structured_field_wins_over_keyword_table():
    """analyzer 的结构化字段优先于关键词表（`_animal_names_from_analysis` 的既有语义）。"""
    # 卡里没有「人味词」的角色，关键词表会把它判成动物
    beast = "[角色锚] 陈浔、黑牛（通体漆黑，四蹄粗壮）；[主体动作] 黑牛反刍；[场景] 山洞内部，黄昏；[镜头] 中景固定"
    # 卡里有「中年壮汉」→ 关键词表**不该**误伤（`_is_animal` 的既有护栏）
    human_like = "[角色锚] 黑牛（中年壮汉，络腮胡，戴斗笠）；[主体动作] 黑牛走进院子；[场景] 山村小院，清晨；[镜头] 中景固定"

    assert recompose_prompt(beast, None)["changed"] is True, "退回关键词表时黑牛会被移出角色锚"
    assert "[角色锚] 黑牛（" in recompose_prompt(human_like, None)["prompt"], "人物卡不该被名字里的「牛」误伤"

    # analyzer 说「这本没有非人角色」（空集）→ 不猜，黑牛留在角色锚里
    declared_empty = recompose_prompt(beast, set())
    assert "[角色锚] 陈浔、黑牛（" in declared_empty["prompt"]
    assert declared_empty["changed"] is False

    # analyzer 指名了大黑牛，但这一镜写的是「黑牛」→ 名字对不上，按字段判定不动它
    declared_other = recompose_prompt(beast, {"大黑牛"})
    assert "[角色锚] 陈浔、黑牛（" in declared_other["prompt"]


def test_scene_already_names_the_animal_no_duplicate_append():
    """场景里已经点名了就不再追加（agnes 是照单执行的，说两遍会被画两遍）。"""
    p = "[角色锚] 陈浔、大黑牛；[主体动作] 一人一牛坐门外；[场景] 山村茅屋废墟，黑牛跪坐门外；[镜头] 中景固定"
    r = recompose_prompt(p, None)
    # 别名「黑牛」已在场景里 → 不追加（否则同一条提示词里说两遍，agnes 会把牛画两头）
    assert "大黑牛" not in r["prompt"]
    assert "[场景] 山村茅屋废墟，黑牛跪坐门外" in r["prompt"]
    assert "不重复追加" in "".join(r["reasons"])
    assert "[场景] 追加" not in "".join(r["reasons"])


def test_recompose_prompts_batch_reports_counts():
    out = recompose_prompts(
        None,
        [
            {"id": "a", "prompt": REAL_OLD_PROMPT},
            {"id": "b", "prompt": REAL_CURRENT_PROMPT},
            {"id": "c", "prompt": ""},
        ],
    )
    assert [n["id"] for n in out["nodes"]] == ["a", "b", "c"]
    assert out["changed_count"] == 1
    assert out["nodes"][2]["skipped"] is True
    assert out["animal_source"].startswith("关键词表")

    analyzed = recompose_prompts(
        {"animal_characters": ["大黑牛"]}, [{"id": "a", "prompt": REAL_OLD_PROMPT}]
    )
    assert analyzed["animal_source"] == "analyzer 结构化字段"
    assert analyzed["changed_count"] == 1

    assert recompose_prompts(None, [])["changed_count"] == 0


def test_analysis_accepts_json_string():
    """前端可能把 analysisJson 原样当字符串传（Java 侧存的就是字符串）。"""
    out = recompose_prompts(
        json.dumps({"animalCharacters": []}), [{"id": "a", "prompt": REAL_OLD_PROMPT}]
    )
    assert out["animal_source"] == "analyzer 结构化字段"
    # 空集 = 分析器明确说没有非人角色 → 大黑牛**留在** [角色锚] 里（不猜）
    assert "大黑牛（" in out["nodes"][0]["prompt"].split("；")[0]

    assert recompose_prompts("不是 JSON", [])["animal_source"].startswith("关键词表")


def test_endpoint_contract():
    """契约：{code, message, data{nodes[{id,prompt,changed,skipped,reasons}], changed_count}}"""
    client = TestClient(app)
    resp = client.post(
        "/v1/novel/recompose-prompts",
        json={
            "analysis": None,
            "nodes": [
                {"id": "img3", "prompt": REAL_CURRENT_PROMPT},
                {"id": "img0", "prompt": REAL_OLD_PROMPT},
                {"id": "n1", "prompt": ""},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0 and body["message"] == "ok"
    data = body["data"]
    assert data["changed_count"] == 1
    assert [n["id"] for n in data["nodes"]] == ["img3", "img0", "n1"]
    assert data["nodes"][0]["changed"] is False
    assert data["nodes"][1]["changed"] is True and data["nodes"][1]["reasons"]
    assert data["nodes"][2]["skipped"] is True
    assert set(data["nodes"][1]) == {"id", "prompt", "changed", "skipped", "reasons"}

    # 空载荷也不能 500（前端可能在没有 imageNode 时误调）
    empty = client.post("/v1/novel/recompose-prompts", json={})
    assert empty.status_code == 200 and empty.json()["data"]["changed_count"] == 0
