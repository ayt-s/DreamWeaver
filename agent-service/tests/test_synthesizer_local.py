"""synthesizer 改用 asset_fetch 已下载的本地文件（A3）。

关键行为：
1. `state["local_video_paths"]` 齐全时**完全不下载**（画布模式由 2 次降到 1 次）
2. 本地路径缺失/不齐时**回落下载**（兼容 A1 之前的旧 Redis 快照与恢复场景，
   以及 asset_fetch 单独失败的情况）—— 不能直接判定失败

⚠️ 测试陷阱：成功分支里 synthesizer 会读 `final_mp4.stat().st_size` 打日志，
所以假 concat 必须真的把输出文件写出来，否则会抛 FileNotFoundError 被 except 吞掉，
测试就在「看似通过」的情况下实际跑的是降级分支。
"""
from pathlib import Path

import pytest

from app.nodes import synthesizer


def _make_fakes(monkeypatch, tmp_path, fail_urls=()):
    """装配 download / concat_videos / _notify_final 三个替身，返回记录容器。"""
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))
    rec = {"downloaded": [], "notified": []}

    async def fake_download(url, dest, timeout=300.0):
        if any(bad in url for bad in fail_urls):
            raise RuntimeError(f"下载失败: {url}")
        rec["downloaded"].append(url)
        Path(dest).write_bytes(b"x" * 100)

    async def fake_concat(inputs, output):
        # 必须真的写出文件：成功分支会 stat(output).st_size
        Path(output).write_bytes(b"FAKEMP4")
        return True

    async def fake_notify(session_id, status, video_urls, error_message=None):
        rec["notified"].append({
            "session_id": session_id, "status": status,
            "video_urls": list(video_urls), "error_message": error_message,
        })

    monkeypatch.setattr(synthesizer, "download", fake_download)
    monkeypatch.setattr(synthesizer, "concat_videos", fake_concat)
    monkeypatch.setattr(synthesizer, "_notify_final", fake_notify)
    return rec


@pytest.mark.asyncio
async def test_uses_local_paths_without_any_download(monkeypatch, tmp_path):
    """本地文件齐全 → 一次网络下载都不该发生。"""
    rec = _make_fakes(monkeypatch, tmp_path)
    d = tmp_path / "s5"
    d.mkdir()
    for i in range(2):
        (d / f"seg_{i:03d}.mp4").write_bytes(b"x" * 100)

    state = {
        "session_id": "s5",
        "segments": [{"prompt": "a"}],
        "video_urls": ["http://a/1.mp4", "http://a/2.mp4"],
        "local_video_paths": [str(d / "seg_000.mp4"), str(d / "seg_001.mp4")],
    }
    out = await synthesizer.synthesizer_node(state)

    assert rec["downloaded"] == []          # 关键断言：不再下载
    assert out["final_video_url"] == "/v1/files/s5/final.mp4"
    assert out["status"] == "completed" or str(out["status"]).endswith("completed")
    assert rec["notified"][0]["status"] == "completed"
    assert rec["notified"][0]["video_urls"][0] == "/v1/files/s5/final.mp4"


@pytest.mark.asyncio
async def test_falls_back_to_download_when_local_missing(monkeypatch, tmp_path):
    """旧快照/恢复场景：local_video_paths 缺失 → 回落下载，不能直接失败。"""
    rec = _make_fakes(monkeypatch, tmp_path)

    state = {
        "session_id": "s6",
        "segments": [{"prompt": "a"}],
        "video_urls": ["http://a/1.mp4", "http://a/2.mp4"],
    }
    out = await synthesizer.synthesizer_node(state)

    assert rec["downloaded"] == ["http://a/1.mp4", "http://a/2.mp4"]
    assert out["final_video_url"] == "/v1/files/s6/final.mp4"


@pytest.mark.asyncio
async def test_partial_local_paths_only_downloads_the_missing_one(monkeypatch, tmp_path):
    """只缺一段 → 只下那一段（不整批重下）。"""
    rec = _make_fakes(monkeypatch, tmp_path)
    d = tmp_path / "s7"
    d.mkdir()
    (d / "seg_000.mp4").write_bytes(b"x" * 100)

    state = {
        "session_id": "s7",
        "segments": [{"prompt": "a"}],
        "video_urls": ["http://a/1.mp4", "http://a/2.mp4"],
        # 第 1 段为空串（asset_fetch 下载失败留下的占位）
        "local_video_paths": [str(d / "seg_000.mp4"), ""],
    }
    out = await synthesizer.synthesizer_node(state)

    assert rec["downloaded"] == ["http://a/2.mp4"]
    assert out["final_video_url"] == "/v1/files/s7/final.mp4"


@pytest.mark.asyncio
async def test_local_file_deleted_on_disk_is_refetched(monkeypatch, tmp_path):
    """路径在 state 里但磁盘上没了（被清理）→ 必须重新下载，不能拿不存在的文件去 concat。"""
    rec = _make_fakes(monkeypatch, tmp_path)
    d = tmp_path / "s8"
    d.mkdir()

    state = {
        "session_id": "s8",
        "segments": [{"prompt": "a"}],
        "video_urls": ["http://a/1.mp4"],
        "local_video_paths": [str(d / "seg_000.mp4")],  # 磁盘上并不存在
    }
    out = await synthesizer.synthesizer_node(state)

    assert rec["downloaded"] == ["http://a/1.mp4"]
    assert out["final_video_url"] == "/v1/files/s8/final.mp4"


@pytest.mark.asyncio
async def test_concat_failure_degrades_to_passthrough(monkeypatch, tmp_path):
    """拼接失败 → 透传原 video_urls + error_message 带回 Java（不标 failed）。"""
    rec = _make_fakes(monkeypatch, tmp_path)
    d = tmp_path / "s9"
    d.mkdir()
    (d / "seg_000.mp4").write_bytes(b"x" * 100)

    async def failing_concat(inputs, output):
        return False

    monkeypatch.setattr(synthesizer, "concat_videos", failing_concat)

    state = {
        "session_id": "s9",
        "segments": [{"prompt": "a"}],
        "video_urls": ["http://a/1.mp4"],
        "local_video_paths": [str(d / "seg_000.mp4")],
    }
    out = await synthesizer.synthesizer_node(state)

    assert out["final_video_url"] == ""
    assert rec["notified"][0]["status"] == "completed"
    assert "拼接失败" in rec["notified"][0]["error_message"]
    # 分段仍透传
    assert rec["notified"][0]["video_urls"] == ["http://a/1.mp4"]
