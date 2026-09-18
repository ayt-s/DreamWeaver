"""`stitch_session` 的 force 语义（2026-09-18）。

## 为什么需要 force

拼接算法本身会修（本轮修掉「多段成片整条没声音」：多段成片走 xfade 时
`filter_complex` 只 map 了视频流，音频从未接进图）。但 `stitch_session` 对
「final.mp4 已存在且不早于最后一个分段」会**幂等短路**直接返回 ——

后果：31 条既有成片（全是无声的）**永远拿不到修复**，用户点「拼接成片」
只会静默拿到旧文件，而重生成分段是要花钱的。

所以人工入口（画廊「重新拼接」）必须能 `force=True` 真的重跑一遍。
"""
from pathlib import Path

import pytest

from app.utils import media, stitch


def _mk(d: Path, n_segs: int = 2, final_older: bool = False) -> Path:
    """造 n 个分段（可选：final.mp4 比分段更新，即"已是最新"）。"""
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_segs):
        (d / f"seg_{i:03d}.mp4").write_bytes(b"x" * 10)
    final = d / "final.mp4"
    if not final_older:
        final.write_bytes(b"y" * 10)   # 默认：final 比最后一个分段新
    else:
        final.write_bytes(b"y" * 10)
    # 让 final 的 mtime 明确晚于分段（默认路径）
    if not final_older:
        import os
        import time
        time.sleep(0.01)
        os.utime(final, None)
    return final


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """替换 media 里被 stitch 用到的函数（stitch 在函数体内 import，故打模块属性）。"""
    calls = {"concat": 0}

    monkeypatch.setattr(media, "session_dir", lambda _sid: tmp_path)
    monkeypatch.setattr(media, "local_url", lambda sid: f"/v1/files/{sid}/final.mp4")

    async def fake_probe(_p):
        return 12.5

    async def fake_concat(clips, final):
        calls["concat"] += 1
        final.write_bytes(b"z" * 100)
        return True

    monkeypatch.setattr(media, "probe_duration", fake_probe)
    monkeypatch.setattr(media, "concat_videos", fake_concat)
    return calls


@pytest.mark.asyncio
async def test_cached_when_final_is_newer(stub, tmp_path):
    """默认（不 force）：final 比最后一个分段新 → 直接返回，不重复编码。"""
    _mk(tmp_path, 2)
    r = await stitch.stitch_session("sid-1")

    assert r is not None
    assert r["cached"] is True
    assert stub["concat"] == 0, "幂等短路时不该再跑 ffmpeg"


@pytest.mark.asyncio
async def test_force_rebuilds_even_when_final_is_newer(stub, tmp_path):
    """force=True：必须真的重拼（这正是既有无声成片拿到修复的唯一途径）。"""
    _mk(tmp_path, 2)
    r = await stitch.stitch_session("sid-1", force=True)

    assert r is not None
    assert r["cached"] is False, "force 必须绕过幂等短路"
    assert stub["concat"] == 1, "force 必须真的调用 concat_videos"


@pytest.mark.asyncio
async def test_rebuild_when_final_is_older_than_segments(stub, tmp_path):
    """final 比分段旧（段重生成过）→ 本来就该重拼，不依赖 force。"""
    final = _mk(tmp_path, 2)
    import os
    import time
    old = time.time() - 1000
    os.utime(final, (old, old))          # final 变旧
    r = await stitch.stitch_session("sid-1")

    assert r is not None and r["cached"] is False
    assert stub["concat"] == 1


@pytest.mark.asyncio
async def test_force_still_requires_two_segments(stub, tmp_path):
    """force 不能绕过「分段不足 2 个」—— 那是硬约束，不是缓存问题。"""
    _mk(tmp_path, 1)
    assert await stitch.stitch_session("sid-1", force=True) is None
    assert stub["concat"] == 0


@pytest.mark.asyncio
async def test_endpoint_passes_force_query(monkeypatch):
    """端点层：?force=true 必须透传到 stitch_session（否则前端按钮点了也没用）。"""
    from fastapi.testclient import TestClient

    from app import main as main_mod

    seen: list[bool] = []

    async def fake_stitch(session_id, video_urls=None, allow_download=True, force=False):
        seen.append(force)
        return {"final_url": f"/v1/files/{session_id}/final.mp4", "segment_count": 2,
                "duration": 3.0, "cached": False}

    async def fake_load(_sid):
        return {}

    monkeypatch.setattr(stitch, "stitch_session", fake_stitch)
    monkeypatch.setattr(main_mod, "session_store", type("S", (), {"load_state": staticmethod(fake_load)})())

    c = TestClient(main_mod.app)
    assert c.post("/v1/tasks/sid-x/concat?force=true").status_code == 200
    assert c.post("/v1/tasks/sid-x/concat").status_code == 200
    assert seen == [True, False], f"force 未按预期透传：{seen}"
