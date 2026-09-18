"""图像定点修正（图生图）端点与网关载荷的回归测试。

## 锁定的契约

- 图生图必须把输入图放 `extra_body.image`：放顶层 `image` 实测 400
  （还抛一个上游 `LLM Provider NOT provided` 的怪错，误导排查方向）
- 本地上传图（`http://localhost:8080/api/uploads/...`）**agnes 拉不到** →
  必须由 agent 抓回来转成 Data URI 再发（官方明确支持 base64 输入）
- 修正失败要**如实报错**（不像质检那样静默降级）：这是用户主动发起的操作
"""
import base64

import httpx
import pytest
from fastapi.testclient import TestClient

from app.controller import image_edit_api as edit_api


class _Gw:
    """记录每次出图的参数，返回假 URL。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail

    async def generate_image(self, prompt, model=None, session_id=None, size=None,
                             ratio=None, seed=None, reference_images=None):
        self.calls.append({"prompt": prompt, "ratio": ratio, "size": size,
                           "reference_images": reference_images})
        if self.fail:
            raise RuntimeError("上游 429")
        return [f"http://mock/fixed{len(self.calls)}.png"]


@pytest.fixture
def client(monkeypatch):
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    from app.main import app
    return TestClient(app), gw


def test_public_url_is_passed_through_verbatim(client):
    c, gw = client
    ref = "https://platform-outputs.agnes-ai.space/images/t2i/task_x/out.png"
    resp = c.post("/v1/images/edit", json={
        "image_url": ref,
        "instruction": "只保留一头黑牛，其余保持不变",
        "ratio": "16:9",
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["urls"] == ["http://mock/fixed1.png"]
    assert gw.calls[0]["reference_images"] == [ref], "公网图应原样透传（agnes 自己会拉）"
    assert gw.calls[0]["ratio"] == "16:9"
    assert "一头黑牛" in gw.calls[0]["prompt"]


def test_local_upload_is_converted_to_data_uri(monkeypatch):
    """本地上传图必须抓成 Data URI —— 否则 agnes 会 400「media must be public」."""
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)

    def handler(request):
        return httpx.Response(200, content=b"\x89PNG-fake",
                              headers={"content-type": "image/png"})

    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(edit_api.httpx, "AsyncClient", factory)

    from app.main import app
    c = TestClient(app)
    resp = c.post("/v1/images/edit", json={
        "image_url": "http://localhost:8080/api/uploads/abc.png",
        "instruction": "去掉多余的道具",
    })

    assert resp.status_code == 200, resp.text
    sent = gw.calls[0]["reference_images"][0]
    assert sent.startswith("data:image/png;base64,"), f"应转成 Data URI，实际 {sent[:40]!r}"
    assert base64.b64decode(sent.split(",", 1)[1]) == b"\x89PNG-fake"


def test_relative_upload_path_gets_java_base(monkeypatch):
    """相对路径（/api/uploads/x.png）要补 Java 基址再抓。"""
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    monkeypatch.setattr(edit_api.settings, "java_notify_url", "http://localhost:8080")
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=b"img",
                              headers={"content-type": "image/jpeg"})

    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    monkeypatch.setattr(edit_api.httpx, "AsyncClient", factory)

    from app.main import app
    c = TestClient(app)
    resp = c.post("/v1/images/edit", json={
        "image_url": "/api/uploads/x.png", "instruction": "改一处"})

    assert resp.status_code == 200, resp.text
    assert seen and seen[0].endswith("/api/uploads/x.png"), seen
    assert gw.calls[0]["reference_images"][0].startswith("data:image/jpeg;base64,")


def test_count_generates_multiple_and_failure_is_reported(monkeypatch):
    """count=2 → 出两张；单张失败不拖垮另一张，全失败才报错。"""
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    from app.main import app
    c = TestClient(app)

    ok = c.post("/v1/images/edit", json={
        "image_url": "https://cdn/x.png", "instruction": "改一处", "count": 2})
    assert ok.status_code == 200
    assert len(ok.json()["data"]["urls"]) == 2

    monkeypatch.setattr(edit_api, "gateway", _Gw(fail=True))
    bad = c.post("/v1/images/edit", json={
        "image_url": "https://cdn/x.png", "instruction": "改一处"})
    assert bad.status_code != 200 or bad.json().get("code") != 0
    detail = bad.text
    assert "修正失败" in detail, f"失败要如实报错（否则用户以为点了没反应）：{detail!r}"


def test_empty_instruction_is_rejected(monkeypatch):
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    from app.main import app
    c = TestClient(app)
    resp = c.post("/v1/images/edit", json={
        "image_url": "https://cdn/x.png", "instruction": "   "})
    assert resp.status_code != 200 or resp.json().get("code") != 0
    assert not gw.calls, "空指令不该真的去打 agnes"
