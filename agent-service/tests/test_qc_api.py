"""质检端点 POST /v1/qc/images。

真图片走夹具（真实出图），下载用 monkeypatch 换掉 —— 端点本身不出网。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.controller import qc_api
from app.main import app

FIX = Path(__file__).parent / "fixtures"
client = TestClient(app)


def _fake_fetch(data: dict[str, bytes]):
    async def _fetch(url: str) -> bytes:
        if url not in data:
            raise RuntimeError("模拟下载失败")
        return data[url]
    return _fetch


def test_接口返回逐张结论与推荐(monkeypatch):
    closeup = (FIX / "frame_face_closeup.jpg").read_bytes()
    ok = (FIX / "frame_mid_shot_ok.jpg").read_bytes()
    monkeypatch.setattr(qc_api, "_fetch_bytes", _fake_fetch({"u1": closeup, "u2": ok}))

    resp = client.post("/v1/qc/images", json={"urls": ["u1", "u2"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert [r["closeup"] for r in data["results"]] == [True, False]
    assert [r["index"] for r in data["results"]] == [0, 1]
    assert data["summary"]["closeupCount"] == 1
    assert data["summary"]["recommendIndex"] == 1


def test_单张下载失败不影响其余(monkeypatch):
    ok = (FIX / "frame_wide_shot_ok.jpg").read_bytes()
    monkeypatch.setattr(qc_api, "_fetch_bytes", _fake_fetch({"good": ok}))

    body = client.post("/v1/qc/images", json={"urls": ["bad", "good"]}).json()
    results = body["data"]["results"]
    assert results[0]["skipped"] is True and results[0]["reason"]
    assert results[1]["skipped"] is False and results[1]["closeup"] is False
    # 序号必须按入参位置，不能因为失败项被挤掉
    assert [r["index"] for r in results] == [0, 1]


@pytest.mark.parametrize("urls", [[], [f"u{i}" for i in range(9)]])
def test_入参数量越界直接422(monkeypatch, urls):
    monkeypatch.setattr(qc_api, "_fetch_bytes", _fake_fetch({}))
    resp = client.post("/v1/qc/images", json={"urls": urls})
    assert resp.status_code == 422, "空列表或超过 MAX_IMAGES 必须被拒"
