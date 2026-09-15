"""notify_final 节点（Task A9）：图的终态只发一次完成回调。

锁定的是「回调时机」这个架构约束：
- 标准模式的回调必须发生在 QC **之后**（否则 Java 侧任务已终态，
  后续 fix_looping 的产物会被 NotifyServiceImpl 的终态检查丢弃）
- 无论走哪条边都恰好发一次（否则任务卡 queued，只能等看门狗兜底）
"""
import asyncio

import pytest

from app.nodes import notify_final as nf


def _spy(monkeypatch):
    calls = []

    async def fake_notify(session_id=None, status=None, video_urls=None,
                          error_message=None, **kwargs):
        calls.append({
            "session_id": session_id, "status": status,
            "video_urls": list(video_urls or []), "error_message": error_message,
        })

    monkeypatch.setattr("app.callback.java_notify.notify_java_completion", fake_notify)
    return calls


def _qc(passed, failed=(), shots=(), total=None):
    """构造 qc_report。total_shots 默认与 shots 长度一致（真实报告必然自洽）。"""
    return {
        "passed": passed,
        "failed_shots": list(failed),
        "total_shots": total if total is not None else len(shots),
        "shots": list(shots),
        "reason": "",
    }


@pytest.mark.asyncio
async def test_all_shots_pass_reports_completed_without_error(monkeypatch):
    calls = _spy(monkeypatch)
    state = {
        "session_id": "n1",
        "video_urls": ["http://a/0.mp4", "http://a/1.mp4"],
        "qc_report": _qc(True),
    }

    out = await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert len(calls) == 1
    assert calls[0]["status"] == "completed"
    assert calls[0]["error_message"] is None
    assert calls[0]["video_urls"] == ["http://a/0.mp4", "http://a/1.mp4"]
    assert out["final_notified"] is True


@pytest.mark.asyncio
async def test_qc_failure_still_completed_but_carries_reason(monkeypatch):
    """QC 判失败 ≠ 任务失败：分段仍可用，如实带回原因但不标 failed。"""
    calls = _spy(monkeypatch)
    state = {
        "session_id": "n2",
        "video_urls": ["http://a/0.mp4", "http://a/1.mp4", "http://a/2.mp4"],
        "qc_report": _qc(False, failed=[1], shots=[
            {"index": 1, "error": "画面质检未通过（黑帧比例 0%，模糊帧比例 90%）"},
        ]),
    }

    out = await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["status"] == "completed"
    assert "未通过质检" in calls[0]["error_message"]
    assert "第 2 镜" in calls[0]["error_message"], "镜号应 1-based 展示"
    assert out["status"] == "completed"


@pytest.mark.asyncio
async def test_qc_summary_caps_listed_shots(monkeypatch):
    """最多列 3 条原因（Java 侧 error_message 是 VARCHAR(512)）。"""
    calls = _spy(monkeypatch)
    shots = [{"index": i, "error": f"错误{i}"} for i in range(6)]
    state = {
        "session_id": "n3",
        "video_urls": [f"http://a/{i}.mp4" for i in range(6)],
        "qc_report": _qc(False, failed=list(range(6)), shots=shots),
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    msg = calls[0]["error_message"]
    assert "6/6 镜未通过质检" in msg
    assert msg.count("第 ") == 3
    assert len(msg) < 512


@pytest.mark.asyncio
async def test_no_video_urls_reports_failed(monkeypatch):
    calls = _spy(monkeypatch)
    state = {
        "session_id": "n4",
        "video_urls": [],
        "video_error": "seg0=提交失败; seg1=提交失败",
    }

    out = await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["status"] == "failed"
    assert "seg0=提交失败" in calls[0]["error_message"]
    assert out["status"] == "failed"


@pytest.mark.asyncio
async def test_no_video_urls_without_reason_uses_default(monkeypatch):
    calls = _spy(monkeypatch)
    await nf.notify_final_node({"session_id": "n5", "video_urls": []})
    await asyncio.sleep(0)

    assert calls[0]["status"] == "failed"
    assert calls[0]["error_message"]


@pytest.mark.asyncio
async def test_qc_missing_falls_back_to_video_error(monkeypatch):
    """画布模式 QC 不跑（qc_report 缺失）→ 退回生成阶段的错误，而不是丢消息。"""
    calls = _spy(monkeypatch)
    state = {
        "session_id": "n6",
        "video_urls": ["http://a/0.mp4"],
        "video_error": "seg2=平台限流",
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["status"] == "completed"
    assert calls[0]["error_message"] == "seg2=平台限流"


@pytest.mark.asyncio
async def test_storyboard_is_sent_as_json(monkeypatch):
    """storyboard 必须带回 Java —— 它被存成 segments_json，是按段重生的输入源。"""
    import json

    calls = []

    async def fake_notify(session_id=None, status=None, video_urls=None,
                          error_message=None, storyboard=None, **kwargs):
        calls.append({"storyboard": storyboard})

    monkeypatch.setattr("app.callback.java_notify.notify_java_completion", fake_notify)
    state = {
        "session_id": "n7",
        "video_urls": ["http://a/0.mp4"],
        "storyboard": [{"prompt_en": "a cat", "seconds": 5, "aspect_ratio": "16:9"}],
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["storyboard"] is not None
    parsed = json.loads(calls[0]["storyboard"])
    assert parsed[0]["prompt_en"] == "a cat"
    # ensure_ascii=False：中文不能变成 \uXXXX（Java 侧 TEXT 列存原文）
    assert "\\u" not in calls[0]["storyboard"]


@pytest.mark.asyncio
async def test_give_up_reason_is_reported_to_user(monkeypatch):
    """自动修复失败时，用户必须知道「系统自己修过 N 轮」而不是以为没人管。"""
    calls = _spy(monkeypatch)
    state = {
        "session_id": "n8",
        "video_urls": ["http://a/0.mp4"],
        "qc_report": _qc(False, failed=[1], shots=[{"index": 1, "error": "画面质检未通过"}]),
        "fix_give_up": True,
        "fix_give_up_reason": "已自动修复 3 轮仍有 1 镜未通过质检",
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    msg = calls[0]["error_message"]
    assert calls[0]["status"] == "completed", "已通过镜仍可用 → 不标 failed"
    assert "未通过质检" in msg
    assert "自动修复 3 轮" in msg


@pytest.mark.asyncio
async def test_give_up_reason_used_alone_when_no_qc_summary(monkeypatch):
    calls = _spy(monkeypatch)
    state = {
        "session_id": "n9",
        "video_urls": ["http://a/0.mp4"],
        "fix_give_up": True,
        "fix_give_up_reason": "会话已中止（任务被删除或重新生成）",
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["error_message"] == "会话已中止（任务被删除或重新生成）"
    assert len(calls[0]["error_message"]) < 512
