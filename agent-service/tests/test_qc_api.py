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


# ------------------------- 候选主体计数 POST /v1/qc/candidates -------------------------

def _fake_vision(replies: dict[int, str], raise_at: set[int] = frozenset()):
    """替掉 `gateway.chat_with_images`：按调用次序返回预设回答。

    `raise_at` 里的次序会抛异常（验证「模型调用失败」也降级成 skipped）。
    """
    class _Gw:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_with_images(self, prompt, image_urls, **kwargs):
            n = self.calls
            self.calls += 1
            if n in raise_at:
                raise RuntimeError("模拟模型调用失败")
            return replies.get(n, "")

    return _Gw()


def test_candidates_逐张给计数与序号(monkeypatch):
    from app.gateway import agnes as agnes_mod

    ok = (FIX / "frame_mid_shot_ok.jpg").read_bytes()
    monkeypatch.setattr(qc_api, "_fetch_bytes", _fake_fetch({"u1": ok, "u2": ok}))
    gw = _fake_vision({
        0: '```json\n{"people": 1, "animals": 2, "face_closeup": false}\n```',
        1: '{"people": 1, "animals": 1, "face_closeup": true}',
    })
    monkeypatch.setattr(agnes_mod, "gateway", gw)

    body = client.post("/v1/qc/candidates", json={"urls": ["u1", "u2"]}).json()

    assert body["code"] == 0
    data = body["data"]
    assert [r["index"] for r in data["results"]] == [0, 1]
    assert [r["animals"] for r in data["results"]] == [2, 1], "两头牛那张必须数出 2"
    assert [r["faceCloseup"] for r in data["results"]] == [False, True]
    assert data["summary"] == {"total": 2, "counted": 2}
    assert all(r["skipped"] is False for r in data["results"])


def test_candidates_下载失败与解析失败都只跳过自己(monkeypatch):
    """任何一环失败都不该 4xx/5xx、也不该挤掉别的候选的序号（质检是附加信息）。"""
    from app.gateway import agnes as agnes_mod

    ok = (FIX / "frame_wide_shot_ok.jpg").read_bytes()
    # bad → 下载失败；junk → 回答不可解析；good → 正常
    monkeypatch.setattr(qc_api, "_fetch_bytes", _fake_fetch({"good": ok, "junk": ok}))
    gw = _fake_vision({0: "我看不清这张图", 1: '{"people": 1, "animals": 1}'})
    monkeypatch.setattr(agnes_mod, "gateway", gw)

    body = client.post("/v1/qc/candidates",
                       json={"urls": ["bad", "junk", "good"]}).json()

    results = body["data"]["results"]
    assert [r["index"] for r in results] == [0, 1, 2], "失败项不能挤掉序号"
    assert results[0]["skipped"] is True and results[0]["reason"] == "图片下载失败"
    assert results[1]["skipped"] is True and "JSON" in results[1]["reason"]
    assert results[2]["skipped"] is False and results[2]["people"] == 1
    assert body["data"]["summary"] == {"total": 3, "counted": 1}


def test_candidates_模型调用异常也降级(monkeypatch):
    from app.gateway import agnes as agnes_mod

    ok = (FIX / "frame_mid_shot_ok.jpg").read_bytes()
    monkeypatch.setattr(qc_api, "_fetch_bytes", _fake_fetch({"u1": ok}))
    monkeypatch.setattr(agnes_mod, "gateway", _fake_vision({}, raise_at={0}))

    body = client.post("/v1/qc/candidates", json={"urls": ["u1"]}).json()
    r = body["data"]["results"][0]
    assert r["skipped"] is True and r["reason"] == "模型调用失败"
    assert body["data"]["summary"]["counted"] == 0


@pytest.mark.parametrize("urls", [[], [f"u{i}" for i in range(qc_api.MAX_CANDIDATES + 1)]])
def test_candidates_入参越界422(monkeypatch, urls):
    monkeypatch.setattr(qc_api, "_fetch_bytes", _fake_fetch({}))
    resp = client.post("/v1/qc/candidates", json={"urls": urls})
    assert resp.status_code == 422
