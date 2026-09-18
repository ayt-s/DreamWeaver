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


# ---- 补画幅（pad_to_ratio）：本地补边 + i2i 扩画幅 ------------------------------
# 背景：老项目（39/40）的图是 1:1 → keyframe 视频是 704x704 方的。补画幅让视频出宽屏
# 而不必重出图。用 data URI 当 image_url，这样测试不需要起 HTTP 服务。


def _png_data_uri(w: int, h: int) -> str:
    import cv2
    import numpy as np
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = (60, 90, 140)
    ok, buf = cv2.imencode(".png", arr)
    assert ok
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()


def test_pad_to_ratio_pads_locally_and_sends_outpaint_instruction(monkeypatch):
    """补画幅：把方图补边到 16:9 后**当 i2i 输入发出去**，并用内置扩画幅提示词。"""
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    from app.main import app
    c = TestClient(app)

    resp = c.post("/v1/images/edit", json={
        "image_url": _png_data_uri(64, 64), "pad_to_ratio": "16:9"})
    assert resp.status_code == 200, resp.text

    call = gw.calls[0]
    sent = call["reference_images"][0]
    assert sent.startswith("data:image/png;base64,"), "补边后的图必须以 data URI 发出"
    assert call["ratio"] == "16:9", "输出画幅要用补的目标画幅"
    assert "两侧" in call["prompt"] and "连续" in call["prompt"], "必须带扩画幅提示词"

    # 补出来的画布尺寸应为该画幅的目标尺寸（16:9 → 2624x1472）
    import cv2
    import numpy as np
    raw = base64.b64decode(sent.split(",", 1)[1])
    arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    assert (arr.shape[1], arr.shape[0]) == edit_api.PAD_TARGETS["16:9"], \
        f"补边尺寸不对：{arr.shape[1]}x{arr.shape[0]}"
    # 纯色图：中心与四角都应是原色（BGR 顺序）—— 若是黑边（BORDER_CONSTANT）四角会是 0
    expect = (60, 90, 140)
    h, w = arr.shape[:2]
    for y, x in ((h // 2, w // 2), (2, 2), (2, w - 3), (h - 3, 2), (h - 3, w - 3)):
        assert tuple(int(v) for v in arr[y, x]) == expect, \
            f"({y},{x}) 不是原色而是 {tuple(arr[y, x])} —— 补边方式不对（可能是黑边）"


def test_pad_to_ratio_skips_when_already_target_ratio(monkeypatch):
    """已经是目标比例就别白跑（模型会顺手重构图，有代价）。"""
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    from app.main import app
    c = TestClient(app)

    resp = c.post("/v1/images/edit", json={
        "image_url": _png_data_uri(160, 90), "pad_to_ratio": "16:9"})
    assert resp.status_code != 200 or resp.json().get("code") != 0
    assert "已经是" in resp.text
    assert not gw.calls, "不该真的去打 agnes"


def test_pad_to_ratio_needs_no_instruction(monkeypatch):
    """补画幅时指令可留空（内置提示词负责），不能报「请写清要改什么」。"""
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    from app.main import app
    c = TestClient(app)

    resp = c.post("/v1/images/edit", json={
        "image_url": _png_data_uri(64, 64), "pad_to_ratio": "16:9", "instruction": ""})
    assert resp.status_code == 200, resp.text
    assert gw.calls, "空指令 + 补画幅应能提交"


def test_pad_to_ratio_appends_user_instruction(monkeypatch):
    """用户额外写了要求时，拼在扩画幅提示词之后（不是覆盖）。"""
    gw = _Gw()
    monkeypatch.setattr(edit_api, "gateway", gw)
    from app.main import app
    c = TestClient(app)

    c.post("/v1/images/edit", json={
        "image_url": _png_data_uri(64, 64), "pad_to_ratio": "16:9",
        "instruction": "顺便把牛去掉"})
    prompt = gw.calls[0]["prompt"]
    assert "两侧" in prompt and prompt.rstrip().endswith("顺便把牛去掉")
