"""分镜忠实度校验：重切一次的行为 + 校验本身失败不得阻断预处理。

不跑真 LLM：analyzer / storyboarder / fidelity 全部替身，只锁「编排逻辑」。
"""
import pytest

from app.novel import fidelity as fid
from app.novel import orchestrator

SEG = {"id": "s1", "chapter": 1, "title": "洞口晒米", "plot": "陈浔在洞口晒米，黑牛在旁",
       "characters": ["陈浔"], "scene": "山洞口，白日", "camera": "中景固定，人物居中",
       "seconds": 5, "mood": "平静"}


def _patch(monkeypatch, *, checks, segments=None, analyze_exc=None):
    """把三段 LLM 全部换成替身；checks 是依次返回的校验结论列表。"""
    calls = {"storyboard": [], "check": 0}

    async def fake_analyze(novel_text, model=None):
        if analyze_exc:
            raise analyze_exc
        return {"summary": "s", "characters": {"陈浔": "青年"}, "scenes": ["山洞口"],
                "visual_style": "电影写实"}

    async def fake_storyboard(novel_text, analysis, target_segments, model, rewrite_hint=""):
        calls["storyboard"].append(rewrite_hint)
        return [dict(segments or SEG)]

    async def fake_check(novel_text, segs, model=None):
        i = calls["check"]
        calls["check"] += 1
        c = checks[min(i, len(checks) - 1)]
        if isinstance(c, Exception):
            raise c
        return dict(c)

    monkeypatch.setattr(orchestrator.analyzer, "analyze", fake_analyze)
    monkeypatch.setattr(orchestrator.storyboarder, "storyboard", fake_storyboard)
    monkeypatch.setattr(orchestrator.fidelity, "check_fidelity", fake_check)
    return calls


PASSED = {"passed": True, "reason": "", "missing": [], "invented": []}
FAILED = {"passed": False, "reason": "漏掉了主角被追杀的主线",
          "missing": ["主角被追杀"], "invented": []}


@pytest.mark.asyncio
async def test_passed_does_not_retry_and_reports_ok(monkeypatch):
    calls = _patch(monkeypatch, checks=[PASSED])
    out = await orchestrator.preprocess_novel("第一章 测试\n陈浔在洞口晒米。", model=object())

    assert len(calls["storyboard"]) == 1, "通过时不该重切"
    assert calls["storyboard"][0] == ""
    assert out["fidelity"]["passed"] is True
    assert out["fidelity"]["attempts"] == 1
    assert out["fidelityWarning"] == ""


@pytest.mark.asyncio
async def test_unfaithful_retries_once_with_hint_and_reports_warning(monkeypatch):
    """不通过 → 带审校意见重切一次；仍不通过也要把警告带给用户（不阻断）。"""
    calls = _patch(monkeypatch, checks=[FAILED, FAILED])
    out = await orchestrator.preprocess_novel("第一章 测试\n陈浔在洞口晒米。", model=object())

    assert len(calls["storyboard"]) == 2, "应重切一次"
    assert calls["storyboard"][0] == "", "首次不带修正意见"
    assert "主角被追杀" in calls["storyboard"][1], "重切必须带上审校意见"
    assert out["fidelity"]["attempts"] == 2
    assert out["fidelity"]["firstAttempt"]["missing"] == ["主角被追杀"]
    assert "忠实度校验未通过" in out["fidelityWarning"]
    assert "重切一次" in out["fidelityWarning"]
    assert "转入画布" in out["fidelityWarning"]
    assert out["segments"], "分镜仍要返回（警告不阻塞出片）"


@pytest.mark.asyncio
async def test_unfaithful_then_passed_keeps_retry_result(monkeypatch):
    """重切后通过 → 用重切那一版，且不再有警告。"""
    calls = _patch(monkeypatch, checks=[FAILED, PASSED], segments={**SEG, "title": "重切版"})
    out = await orchestrator.preprocess_novel("第一章 测试\n正文。", model=object())

    assert len(calls["storyboard"]) == 2
    assert out["fidelity"]["passed"] is True
    assert out["fidelityWarning"] == ""
    assert out["segments"][0]["title"] == "重切版", "应保留重切结果"


@pytest.mark.asyncio
async def test_check_failure_does_not_break_preprocess(monkeypatch):
    """校验本身挂了（LLM 抖动）→ 预处理必须照常出结果，只是没有结论。"""
    calls = _patch(monkeypatch, checks=[RuntimeError("LLM 超时")])
    out = await orchestrator.preprocess_novel("第一章 测试\n正文。", model=object())

    assert len(calls["storyboard"]) == 1, "校验失败不该触发重切"
    assert out["segments"], "预处理结果必须照常返回"
    assert out["fidelity"]["passed"] is None
    assert "LLM 超时" in out["fidelity"]["error"]
    assert out["fidelityWarning"] == ""


@pytest.mark.asyncio
async def test_retry_failure_falls_back_to_first_version(monkeypatch):
    """重切本身失败 → 保留第一版分镜 + 第一版结论（不能把分镜弄丢）。"""
    calls = {"n": 0}

    async def fake_analyze(novel_text, model=None):
        return {"summary": "s", "characters": {}, "scenes": [], "visual_style": "写实"}

    async def fake_storyboard(novel_text, analysis, target_segments, model, rewrite_hint=""):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("重切时 LLM 挂了")
        return [dict(SEG)]

    async def fake_check(novel_text, segs, model=None):
        return dict(FAILED)

    monkeypatch.setattr(orchestrator.analyzer, "analyze", fake_analyze)
    monkeypatch.setattr(orchestrator.storyboarder, "storyboard", fake_storyboard)
    monkeypatch.setattr(orchestrator.fidelity, "check_fidelity", fake_check)

    out = await orchestrator.preprocess_novel("第一章 测试\n正文。", model=object())
    assert calls["n"] == 2
    assert out["segments"][0]["id"] == "s1", "重切失败必须回退到第一版分镜"
    assert "忠实度校验未通过" in out["fidelityWarning"]


def test_warning_text_is_silent_when_unknown_or_passed():
    assert fid.warning_text({"passed": True}) == ""
    assert fid.warning_text({"passed": None, "error": "x"}) == ""
    assert fid.warning_text({}) == ""


def test_warning_text_caps_length():
    huge = {"passed": False, "reason": "长" * 300,
            "missing": ["缺" * 120] * 5, "invented": ["编" * 120] * 5, "attempts": 1}
    text = fid.warning_text(huge)
    assert len(text) <= fid.MAX_WARNING_CHARS


def test_rewrite_hint_lists_both_kinds():
    hint = fid.rewrite_hint({"missing": ["缺 A"], "invented": ["编 B"], "reason": "不忠实"})
    assert "缺 A" in hint and "编 B" in hint and "不忠实" in hint
