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


def _stub_stitch(monkeypatch, *, result=None, exc=None, calls=None):
    """替换 app.utils.stitch.stitch_session（不跑真 ffmpeg）。

    calls 非空时记录调用参数 —— 用来断言「画布模式/单段不该调用拼接」这类否定条件。
    """
    async def fake(session_id, video_urls=None, allow_download=True):
        if calls is not None:
            calls.append({"session_id": session_id, "video_urls": list(video_urls or [])})
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr("app.utils.stitch.stitch_session", fake)


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
async def test_standard_mode_auto_stitches_final_first(monkeypatch):
    """标准模式（无 segments）产出分段 → 自动拼接，成片排在首位。

    画廊 `finalVideoUrl()` 按 /v1/files/ 前缀识别成片，插首位即自动切成
    「成片 + 分段缩略」布局 —— 用户不用再去任务卡点「拼接成片」。
    """
    calls = _spy(monkeypatch)
    _stub_stitch(monkeypatch, result={
        "final_url": "/v1/files/s1/final.mp4", "segment_count": 2,
        "duration": 8.4, "cached": False,
    })
    state = {
        "session_id": "s1",
        "video_urls": ["http://a/0.mp4", "http://a/1.mp4"],
        "qc_report": _qc(True),
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["status"] == "completed"
    assert calls[0]["video_urls"][0] == "/v1/files/s1/final.mp4"
    assert len(calls[0]["video_urls"]) == 3, "分段必须保留（可单独下载/段重生）"
    assert calls[0]["error_message"] is None


@pytest.mark.asyncio
async def test_canvas_mode_does_not_stitch(monkeypatch):
    """画布模式（segments 非空）已由 synthesizer 拼接，notify_final 不能重复拼。"""
    _spy(monkeypatch)
    stitched = []
    _stub_stitch(monkeypatch, result={"final_url": "/dup.mp4", "segment_count": 3},
                 calls=stitched)
    state = {
        "session_id": "s2",
        "video_urls": [f"http://a/{i}.mp4" for i in range(3)],
        "segments": [{"image_url": "u", "prompt": "p", "seconds": 4}],
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert stitched == [], "画布模式不应调用 stitch（会拼第二遍）"


@pytest.mark.asyncio
async def test_single_segment_skips_stitch(monkeypatch):
    """单段无需拼接（拼接端点本身也要求 ≥2 段）。"""
    _spy(monkeypatch)
    stitched = []
    _stub_stitch(monkeypatch, result={"final_url": "/x.mp4", "segment_count": 1}, calls=stitched)
    state = {"session_id": "s3", "video_urls": ["http://a/0.mp4"]}

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert stitched == []


@pytest.mark.asyncio
async def test_stitch_failure_keeps_segments_and_reports_reason(monkeypatch):
    """拼接失败不阻断任务：分段保留、仍 completed、原因可见（与 synthesizer 同一降级哲学）。"""
    calls = _spy(monkeypatch)
    _stub_stitch(monkeypatch, exc=RuntimeError("ffmpeg 编码未成功"))
    state = {"session_id": "s4", "video_urls": ["http://a/0.mp4", "http://a/1.mp4"]}

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["status"] == "completed"
    assert calls[0]["video_urls"] == ["http://a/0.mp4", "http://a/1.mp4"]
    assert "自动拼接失败" in calls[0]["error_message"]
    assert "ffmpeg" in calls[0]["error_message"]
    assert len(calls[0]["error_message"]) < 512, "Java 侧 error_message 是 VARCHAR(512)"


@pytest.mark.asyncio
async def test_stitch_failure_appends_to_qc_reason(monkeypatch):
    """QC 已有原因时不能把它挤掉，拼接原因要追加在后面。"""
    calls = _spy(monkeypatch)
    _stub_stitch(monkeypatch, exc=RuntimeError("磁盘已满"))
    state = {
        "session_id": "s5",
        "video_urls": ["http://a/0.mp4", "http://a/1.mp4"],
        "qc_report": _qc(False, failed=[1], shots=[{"index": 1, "error": "模糊帧比例 90%"}]),
    }

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    msg = calls[0]["error_message"]
    assert "未通过质检" in msg and "自动拼接失败" in msg


@pytest.mark.asyncio
async def test_stitch_cached_result_is_also_prepended(monkeypatch):
    """已存在成片（cached=True）同样要插到首位，否则画廊仍显示「无成片」。"""
    calls = _spy(monkeypatch)
    _stub_stitch(monkeypatch, result={
        "final_url": "/v1/files/s6/final.mp4", "segment_count": 2,
        "duration": 8.0, "cached": True,
    })
    state = {"session_id": "s6", "video_urls": ["http://a/0.mp4", "http://a/1.mp4"]}

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["video_urls"][0] == "/v1/files/s6/final.mp4"


@pytest.mark.asyncio
async def test_auto_stitch_disabled_by_config(monkeypatch):
    """auto_stitch_enabled=False → 不拼接（留人工入口，行为退回改造前）。"""
    calls = _spy(monkeypatch)
    stitched = []
    _stub_stitch(monkeypatch, result={"final_url": "/x.mp4", "segment_count": 2}, calls=stitched)
    monkeypatch.setattr("app.utils.stitch.stitch_enabled", lambda: False)
    state = {"session_id": "s7", "video_urls": ["http://a/0.mp4", "http://a/1.mp4"]}

    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert stitched == []
    assert calls[0]["video_urls"] == ["http://a/0.mp4", "http://a/1.mp4"]


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
