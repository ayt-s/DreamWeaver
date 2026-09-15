"""asset_fetch 节点：把 agnes 返回的公网视频直链落到本地。

为什么需要它（P0-1 QC 落地的前置）：
- QC 只能对本地文件跑 cv2 / ffmpeg，而 agnes 返回的是公网直链
- 原架构里唯一的下载发生在 synthesizer 内部，而标准模式**根本不经过 synthesizer**
  （video_generator → qc_checker → END），于是 QC 永远只能 skip 返回 passed
- 下载前置为独立节点后，QC 才有本地文件可检；synthesizer 也能直接吃本地文件

索引对齐硬约束：local_video_paths 与 video_urls 严格同长同序，
下载失败的索引留空串（**不压缩数组**），否则下游按索引取段会错位 ——
这正是历史上「段索引错位」那类 bug 的根源（见提交 949cf41）。
"""
from pathlib import Path

import pytest

from app.nodes import asset_fetch


@pytest.mark.asyncio
async def test_fetch_downloads_all_and_writes_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))
    calls = []

    async def fake_download(url, dest, timeout=300.0):
        calls.append((url, str(dest)))
        Path(dest).write_bytes(b"x" * 10)

    monkeypatch.setattr(asset_fetch, "download", fake_download)
    state = {"session_id": "s1", "video_urls": ["http://a/1.mp4", "http://a/2.mp4"]}

    out = await asset_fetch.asset_fetch_node(state)

    assert len(calls) == 2
    assert calls[0][0] == "http://a/1.mp4"
    # 类型契约：list[str]，不是 Path 对象（与 state 声明和 synthesizer 的读取方式一致）
    assert all(isinstance(p, str) for p in out["local_video_paths"])
    assert [Path(p).name for p in out["local_video_paths"]] == ["seg_000.mp4", "seg_001.mp4"]
    assert all(Path(p).exists() for p in out["local_video_paths"])


@pytest.mark.asyncio
async def test_fetch_reuses_existing_local_file(monkeypatch, tmp_path):
    """同一会话重复进入（恢复场景）不重复下载 —— 否则白花带宽与时间。"""
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))
    d = tmp_path / "s2"
    d.mkdir()
    (d / "seg_000.mp4").write_bytes(b"y" * 20)
    calls = []

    async def fake_download(url, dest, timeout=300.0):
        calls.append(url)
        Path(dest).write_bytes(b"x")

    monkeypatch.setattr(asset_fetch, "download", fake_download)
    state = {"session_id": "s2", "video_urls": ["http://a/1.mp4"]}

    out = await asset_fetch.asset_fetch_node(state)

    assert calls == []  # 已存在且非空 → 跳过下载
    assert out["local_video_paths"] == [str(d / "seg_000.mp4")]


@pytest.mark.asyncio
async def test_fetch_empty_urls_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))
    out = await asset_fetch.asset_fetch_node({"session_id": "s3", "video_urls": []})
    assert out["local_video_paths"] == []


@pytest.mark.asyncio
async def test_fetch_partial_failure_keeps_index_alignment(monkeypatch, tmp_path):
    """单镜下载失败 → 该索引留空串占位，不压缩数组，也不抛异常。"""
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))

    async def fake_download(url, dest, timeout=300.0):
        if "bad" in url:
            raise RuntimeError("网络炸了")
        Path(dest).write_bytes(b"x")

    monkeypatch.setattr(asset_fetch, "download", fake_download)
    state = {"session_id": "s4", "video_urls": ["http://a/1.mp4", "http://a/bad.mp4"]}

    out = await asset_fetch.asset_fetch_node(state)

    assert len(out["local_video_paths"]) == 2
    assert Path(out["local_video_paths"][0]).name == "seg_000.mp4"
    assert out["local_video_paths"][1] == ""  # 失败位置留空占位


@pytest.mark.asyncio
async def test_fetch_treats_zero_byte_file_as_missing(monkeypatch, tmp_path):
    """0 字节残留文件必须重新下载（上次下载中断的痕迹）。"""
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))
    d = tmp_path / "s5"
    d.mkdir()
    (d / "seg_000.mp4").write_bytes(b"")  # 空文件
    calls = []

    async def fake_download(url, dest, timeout=300.0):
        calls.append(url)
        Path(dest).write_bytes(b"x" * 10)

    monkeypatch.setattr(asset_fetch, "download", fake_download)
    out = await asset_fetch.asset_fetch_node(
        {"session_id": "s5", "video_urls": ["http://a/1.mp4"]})

    assert calls == ["http://a/1.mp4"]
    assert out["local_video_paths"][0] != ""
