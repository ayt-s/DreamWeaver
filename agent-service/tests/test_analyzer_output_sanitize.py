"""analyzer 输出清洗：中文描述里的英文杂质要清掉，但术语要留（P1-5）。

★ 实测来源：角色卡里出现 `左角齐断只剩 stump` —— 它一路进了锚定图提示词与画面提示词。
对中文图像模型来说这是纯噪声（也说明提示词里混了不成句的内容）。
"""
from app.novel import analyzer
from app.novel.analyzer import NovelAnalysis, _sanitize_analysis, _strip_ascii_noise

BASE = {
    "summary": "少年与断角黑牛",
    "characters": {"大黑牛": "通体漆黑的灵兽牛，左角齐根断去只剩 stump，右角完整尖锐"},
    "scenes": ["小山坡，白日"],
    "tone": "轻松",
    "visual_style": "3D 写实国漫",
}


class _FakeResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self, output):
        self._output = output

    async def run(self, _text):
        return _FakeResult(self._output)


def test_英文杂质被清掉且标点不破():
    assert _strip_ascii_noise("左角齐根断去只剩 stump，右角完整尖锐") == "左角齐根断去只剩，右角完整尖锐"


def test_术语白名单保留():
    """4K / 3D / HD 是有效信息，删了反而丢画质约束。"""
    assert "4K" in _strip_ascii_noise("4K 超高清，3D 写实")
    assert _strip_ascii_noise("3D 写实国漫") == "3D 写实国漫"


def test_句首句尾的破碎标点被收干净():
    assert _strip_ascii_noise("only 少年站在门口") == "少年站在门口"
    assert _strip_ascii_noise("少年站在门口 down") == "少年站在门口"
    assert _strip_ascii_noise("少年，，站在门口") == "少年，站在门口"


def test_整个analysis的角色卡都被清洗():
    out = _sanitize_analysis(dict(BASE, characters=dict(BASE["characters"])))
    assert "stump" not in out["characters"]["大黑牛"]
    assert out["scenes"] == ["小山坡，白日"]


def test_角色键名被洗成空串时保留原名():
    """宁可留个怪名字，也不能让 characters 的键变空 —— 下游按名字匹配，空键等于丢角色。"""
    out = _sanitize_analysis(dict(BASE, characters={"ab": "少年", "陈浔": "少年"}))
    assert set(out["characters"].keys()) == {"ab", "陈浔"}


def test_列表字段与风格字段一起洗():
    out = _sanitize_analysis(
        dict(BASE, scenes=["小山坡 white sky"], props=["开山斧 blade"], visual_style="anime 国风")
    )
    assert out["scenes"] == ["小山坡 sky"] or out["scenes"] == ["小山坡"]
    assert "blade" not in out["props"][0]
    assert "anime" not in out["visual_style"]


async def test_analyze_端到端清洗(monkeypatch):
    monkeypatch.setattr(
        analyzer, "_build_agent", lambda model: _FakeAgent(NovelAnalysis.model_validate(BASE))
    )
    out = await analyzer.analyze("正文", model=object())
    assert "stump" not in out["characters"]["大黑牛"]
    # 清洗不能把「漏填 animal_characters」的语义又变回来
    assert "animal_characters" not in out


async def test_清洗不会动动物字段的漏填语义(monkeypatch):
    class Fake:
        def __init__(self, output):
            self.output = output

        async def run(self, _text):
            return _FakeResult(self.output)

    monkeypatch.setattr(
        analyzer,
        "_build_agent",
        lambda model: Fake(NovelAnalysis.model_validate({**BASE, "animal_characters": ["大黑牛"]})),
    )
    out = await analyzer.analyze("正文", model=object())
    assert out["animal_characters"] == ["大黑牛"]
