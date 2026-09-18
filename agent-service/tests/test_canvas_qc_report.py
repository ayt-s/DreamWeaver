"""画布模式的质检结论必须真的回到 Java（2026-09-18 起画布链路也过 QC）。

背景：`_asset_route` 此前把画布模式直接接到 `synthesizer`（`segments → synthesize → END`），
于是用户主用的「小说→画布→成片」链路**从来没有被体检过**。现在两种模式都过 QC，
但画布模式的回调由 `synthesizer` 发出 —— 质检结论若不随那条回调带上，
等于跑了一趟白跑：用户看不到「哪一段没通过、为什么」。

`notify_final` 与 `synthesizer` 共用同一个汇总函数（`summarize_qc_report`），
就是为了避免「两处各写一份必然漂移」。
"""
import pytest

from app.nodes import synthesizer as syn_mod
from app.state import TaskStatus


def _state(**extra) -> dict:
    state = {
        "session_id": "s-canvas-qc",
        "user_id": "t",
        "raw_prompt": "画布",
        "segments": [{"prompt": "第一段"}, {"prompt": "第二段"}],
        "video_urls": ["http://mock/seg0.mp4", "http://mock/seg1.mp4"],
        "local_video_paths": [],
        "storyboard": [{"shot_id": 0}, {"shot_id": 1}],
        "trace": [],
    }
    state.update(extra)
    return state


@pytest.mark.asyncio
async def test_canvas_completion_callback_carries_qc_summary(monkeypatch, tmp_path):
    """QC 判失败时，合成回调的 error_message 必须写明「哪几镜、为什么」。"""
    captured: dict = {}

    async def _fake_notify(session_id, status, video_urls, error_message=None):
        captured.update(session_id=session_id, status=status,
                        urls=list(video_urls), error=error_message)

    async def _fake_concat(files, dest):
        dest.write_bytes(b"x")
        return True

    # 本地分段文件：synthesizer 优先复用本地产物，给了就不用下载
    segs = []
    for i in range(2):
        p = tmp_path / f"seg_{i:03d}.mp4"
        p.write_bytes(b"x")
        segs.append(str(p))

    monkeypatch.setattr(syn_mod, "_notify_final", _fake_notify)
    monkeypatch.setattr(syn_mod, "concat_videos", _fake_concat)
    monkeypatch.setattr(syn_mod, "session_dir", lambda sid: tmp_path)
    monkeypatch.setattr(syn_mod, "local_url", lambda sid: "/v1/files/s/final.mp4")

    qc_report = {
        "passed": False,
        "total_shots": 2,
        "shots": [
            {"index": 0, "error": "画面质检未通过（模糊帧比例 100%）"},
            {"index": 1, "error": ""},
        ],
        "failed_shots": [0],
    }

    out = await syn_mod.synthesizer_node(_state(qc_report=qc_report, local_video_paths=segs))

    assert out["status"] == TaskStatus.COMPLETED
    assert captured["status"] == "completed"
    # 长视频在前、分段在后（Java 的 result_json 口径）
    assert captured["urls"][0].endswith("final.mp4")
    assert captured["urls"][1:] == ["http://mock/seg0.mp4", "http://mock/seg1.mp4"]
    assert captured["error"], "质检结论必须随回调回 Java，否则用户看不到"
    assert "1/2" in captured["error"], f"应写明未通过镜数，实际: {captured['error']!r}"
    assert "模糊" in captured["error"], f"应带上原因，实际: {captured['error']!r}"


@pytest.mark.asyncio
async def test_canvas_completion_callback_is_clean_when_qc_passes(monkeypatch, tmp_path):
    """QC 全过时不加噪音（避免用户以为出了问题）。"""
    captured: dict = {}

    async def _fake_notify(session_id, status, video_urls, error_message=None):
        captured.update(error=error_message)

    async def _fake_concat(files, dest):
        dest.write_bytes(b"x")
        return True

    files = []
    for i in range(2):
        q = tmp_path / f"seg_{i:03d}.mp4"
        q.write_bytes(b"x")
        files.append(str(q))

    monkeypatch.setattr(syn_mod, "_notify_final", _fake_notify)
    monkeypatch.setattr(syn_mod, "concat_videos", _fake_concat)
    monkeypatch.setattr(syn_mod, "session_dir", lambda sid: tmp_path)
    monkeypatch.setattr(syn_mod, "local_url", lambda sid: "/v1/files/s/final.mp4")

    await syn_mod.synthesizer_node(
        _state(qc_report={"passed": True, "failed_shots": [], "shots": []},
               local_video_paths=files))

    assert not captured["error"]
