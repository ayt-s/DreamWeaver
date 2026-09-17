"""analyzer 的 animal_characters：**漏填 ≠ 明确说没有**。

背景（2026-09-17 复查）：
`NovelAnalysis.animal_characters` 有 `default_factory=list`，实测它**不在** JSON schema 的
`required` 里 —— 模型可以合法漏填，pydantic 会补成 `[]`。而 composer 把「空数组」读成
「分析器明确说了这本书没有非人角色」→ **关掉关键词兜底** → 大黑牛回到 [角色锚] →
两头牛的 bug 静默复发（与「字段不存在 → 退回关键词猜」是两条完全不同的路）。

所以 analyze() 必须把「漏填」变成**键不存在**，而不是留下一个 `[]`。
"""
from app.novel import analyzer
from app.novel.analyzer import NovelAnalysis
from app.novel.composer import compose_image_prompt

BASE = {
    "summary": "少年与断角黑牛在山坡上",
    "characters": {"大黑牛": "通体漆黑的灵兽牛，左角齐根断去，右角完整尖锐"},
    "scenes": ["小山坡，白日，微风吹过万木倾伏"],
    "tone": "轻松",
    "visual_style": "3D 写实国漫",
}

SEG = {
    "plot": "黑牛反刍保下些许大米",
    "scene": "小山坡，白日",
    "camera": "中景固定",
    "characters": ["大黑牛"],
    "mood": "轻松",
}


def _anchor(prompt: str) -> str:
    return prompt.split("[角色锚]")[1].split("；")[0]


class _FakeResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self, output):
        self._output = output

    async def run(self, _text):
        return _FakeResult(self._output)


def _stub(monkeypatch, output):
    monkeypatch.setattr(analyzer, "_build_agent", lambda model: _FakeAgent(output))


async def test_模型漏填时不留下空数组_让下游退回关键词兜底(monkeypatch):
    """★ 核心：漏填必须表现为「键不存在」，否则两条语义被合并、bug 静默复发。"""
    _stub(monkeypatch, NovelAnalysis.model_validate(BASE))  # 模型没给这个字段
    out = await analyzer.analyze("正文", model=object())
    assert "animal_characters" not in out, "漏填不能变成 []"
    # 下游据此才走关键词兜底 —— 大黑牛照样不进角色锚
    p = compose_image_prompt(SEG, "3D 写实国漫", out)
    assert "大黑牛" not in _anchor(p)


async def test_模型明确填空数组时保留空数组_不许再猜(monkeypatch):
    """另一端：模型答了「没有非人角色」，就要信它（人类绰号「黑牛」靠它保护）。"""
    _stub(monkeypatch, NovelAnalysis.model_validate({**BASE, "animal_characters": []}))
    out = await analyzer.analyze("正文", model=object())
    assert out["animal_characters"] == []


async def test_模型给了动物名单时原样带出(monkeypatch):
    _stub(monkeypatch, NovelAnalysis.model_validate({**BASE, "animal_characters": ["大黑牛"]}))
    out = await analyzer.analyze("正文", model=object())
    assert out["animal_characters"] == ["大黑牛"]
    p = compose_image_prompt(SEG, "3D 写实国漫", out)
    assert "大黑牛" not in _anchor(p)
