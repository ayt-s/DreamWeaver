"""首帧锁定（keyframe）与出图画幅（size/ratio）的契约锁定。

## 这些测试在防什么（都是 2026-09-18 实测出来的真实缺陷）

1. **出图不传画幅** → 服务端按 1:1 出图。实测项目真实产物 **18/18 都是 1024x1024
   正方形**，而视频链路是 16:9：首帧是画面的真正基底，正方形基底会被视频模型
   先重构图一次。显式传 `size="2K", ratio="16:9"` 实测得到 2624x1472。
2. **视频从没用过 keyframe** → 全仓 grep `first_frame|keyframe` 只有一处注释。
   官方对 reference 的定义是「内容/风格/运动参考，**可能重新构图、重新计时**」，
   也就是说用户认可的首帧图根本不是视频起点（「视频和我出的图不像」是预期行为）。

契约要点（改代码时不要破坏）：
- `mode` 与媒体字段**互斥**：keyframe 不许带 images，reference 不许带 first/last_frame。
  网关必须自己兜住（一次 400 = 白等一轮重试 + 该镜没产物），所以这里有降级断言。
- Flash **硬限 720P**：请求别的档要按 720P 发，否则 400 `size must be 720P`。
- 画幅是白名单清洗过的：脏值会让 agnes 直接 400，不能透传。
"""
import asyncio
import json

import httpx
import pytest

from app.gateway import agnes
from app.gateway.agnes import AgnesGateway
from app.utils.prompting import (
    normalize_image_ratio,
    normalize_image_size,
    normalize_video_size,
)


# ---------------------------------------------------------------- 白名单清洗

def test_ratio_whitelist_normalizes_fullwidth_and_dirty_values():
    assert normalize_image_ratio("16:9") == "16:9"
    # 全角冒号（前端/中文输入法很容易带出来）
    assert normalize_image_ratio("16：9") == "16:9"
    assert normalize_image_ratio(" 9:16 ") == "9:16"
    # 白名单外一律回落默认，绝不透传（agnes 对非法取值直接 400）
    assert normalize_image_ratio("auto") == "16:9"
    assert normalize_image_ratio("1280x720") == "16:9"
    assert normalize_image_ratio(None, "1:1") == "1:1"


def test_size_tier_whitelist():
    assert normalize_image_size("2k") == "2K"
    assert normalize_image_size("4K") == "4K"
    assert normalize_image_size("1280x720") == "2K"      # 精确像素值不被透传
    assert normalize_video_size("2k") == "2K"
    assert normalize_video_size("1080P") == "720P"       # 不支持的档回落


# ---------------------------------------------------------------- 网关替身

class _FakeResp:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]


class _Recorder:
    """记录每次 POST 的 (path, payload)，按序吐出预设响应。"""

    def __init__(self, responses: list[_FakeResp]) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._responses = list(responses)

    async def post(self, url: str, **kwargs) -> _FakeResp:
        self.calls.append((url, kwargs.get("json") or {}))
        return self._responses.pop(0)


class _FakeAgnesClient:
    def __init__(self, recorder: _Recorder) -> None:
        self.name = "intl"
        self.base_url = "http://fake.agnes"
        self._client = recorder


class _NoopGate:
    """替掉真实 VideoSubmitGate —— 它默认等 35s 间隔，测试里必须跳过。"""

    async def acquire(self) -> None:
        return None


def _make_gateway(recorder: _Recorder) -> AgnesGateway:
    """绕开 __init__：不碰真实端点与凭据。"""
    gw = AgnesGateway.__new__(AgnesGateway)
    gw.providers = {"intl": _FakeAgnesClient(recorder)}
    gw.provider_names = ["intl"]
    gw._session_provider = {}
    gw._rr_index = 0
    gw._rr_lock = asyncio.Lock()
    return gw


# ---------------------------------------------------------------- 出图：画幅/尺寸

@pytest.mark.real_gateway_method  # 驱动真实 generate_image；HTTP 客户端已换替身，不出网
@pytest.mark.asyncio
async def test_generate_image_payload_carries_size_and_ratio():
    rec = _Recorder([_FakeResp(200, {"data": [{"url": "http://x/a.png"}]})])
    gw = _make_gateway(rec)

    urls = await gw.generate_image(prompt="一只猫", ratio="9:16", size="2K")

    path, payload = rec.calls[0]
    assert path == "/images/generations"
    assert payload["ratio"] == "9:16", "画幅必须显式发出去，否则服务端给 1:1 正方形"
    assert payload["size"] == "2K"
    # URL 输出必须走 extra_body（顶层 response_format 会 400）
    assert payload["extra_body"]["response_format"] == "url"
    assert payload["model"]
    assert urls == ["http://x/a.png"]


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_generate_image_falls_back_on_dirty_ratio():
    """脏画幅不能透传：agnes 对非法取值是 400（一次 400 = 一张图白等）。"""
    rec = _Recorder([_FakeResp(200, {"data": [{"url": "http://x/a.png"}]})])
    gw = _make_gateway(rec)

    await gw.generate_image(prompt="x", ratio="auto")

    _, payload = rec.calls[0]
    assert payload["ratio"] == agnes.settings.default_aspect_ratio


# ---------------------------------------------------------------- 视频：模式互斥

@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_submit_video_keyframe_sends_frames_not_images(monkeypatch):
    rec = _Recorder([_FakeResp(200, {"id": "vid1"})])
    gw = _make_gateway(rec)
    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())

    await gw.submit_video(
        prompt="少年走过田埂", mode="keyframe", session_id="s1",
        first_frame="http://x/first.png", last_frame="http://x/last.png",
        reference_images=["http://x/ref.png"],   # 上游可能同时给；网关必须丢掉
    )

    _, payload = rec.calls[0]
    assert payload["mode"] == "keyframe"
    assert payload["first_frame"] == "http://x/first.png"
    assert payload["last_frame"] == "http://x/last.png"
    assert "images" not in payload, "keyframe 与 images 互斥，带了会被平台 400"


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_submit_video_keyframe_without_frames_downgrades_to_reference(monkeypatch):
    """没有帧的 keyframe 必是 400 —— 降级到 reference（有图）/ text（无图），别白等重试。"""
    rec = _Recorder([_FakeResp(200, {"id": "vid1"})])
    gw = _make_gateway(rec)
    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())

    await gw.submit_video(prompt="x", mode="keyframe", session_id="s1",
                          reference_images=["http://x/ref.png"])

    _, payload = rec.calls[0]
    assert payload["mode"] == "reference"
    assert payload["images"] == ["http://x/ref.png"]
    assert "first_frame" not in payload


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_submit_video_reference_drops_frame_fields(monkeypatch):
    rec = _Recorder([_FakeResp(200, {"id": "vid1"})])
    gw = _make_gateway(rec)
    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())

    await gw.submit_video(prompt="x", mode="reference", session_id="s1",
                          reference_images=["http://x/ref.png"],
                          first_frame="http://x/first.png")

    _, payload = rec.calls[0]
    assert payload["mode"] == "reference"
    assert "first_frame" not in payload, "reference 模式不接受 first_frame"
    assert "last_frame" not in payload


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_submit_video_size_is_clamped_per_model(monkeypatch):
    """Flash 硬限 720P（别的档 400）；非 Flash 才吃 2K。"""
    rec = _Recorder([_FakeResp(200, {"id": "v1"}), _FakeResp(200, {"id": "v2"})])
    gw = _make_gateway(rec)
    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())

    await gw.submit_video(prompt="x", session_id="s1",
                          model="agnes-video-2.5-flash", size="2K")
    await gw.submit_video(prompt="x", session_id="s1",
                          model="agnes-video-2.5", size="2K")

    assert rec.calls[0][1]["size"] == "720P", "Flash 传 2K 会被平台 400"
    assert rec.calls[1][1]["size"] == "2K", "HD 模型应当保留请求的分辨率档"


# ---------------------------------------------------------------- 画布分镜：模式决策

def _canvas_state(**extra) -> dict:
    state = {
        "session_id": "s-canvas",
        "user_id": "tester",
        "raw_prompt": "画布",
        "segments": [
            {"image_url": "http://mock/img/a.png", "prompt": "第一段", "seconds": 5},
            {"image_url": "http://mock/img/b.png", "prompt": "第二段", "seconds": 6},
        ],
        "trace": [],
    }
    state.update(extra)
    return state


async def _fake_translate(text: str) -> str:
    return f"EN[{text[:12]}]"


@pytest.mark.asyncio
async def test_canvas_storyboarder_locks_first_frame_by_default(monkeypatch):
    from app.nodes import storyboard as sb
    monkeypatch.setattr(sb, "translate_to_en", _fake_translate)

    out = await sb.canvas_storyboarder_node(_canvas_state())

    shots = out["storyboard"]
    assert [s["mode"] for s in shots] == ["keyframe", "keyframe"]
    assert shots[0]["first_frame"] == "http://mock/img/a.png"
    assert shots[1]["first_frame"] == "http://mock/img/b.png"
    # 段间衔接默认关：不该凭空出现尾帧
    assert "last_frame" not in shots[0]


@pytest.mark.asyncio
async def test_canvas_storyboarder_lock_off_falls_back_to_reference(monkeypatch):
    """关掉锁定 = 回到旧行为：首帧图只当参考图（reference）。"""
    from app.nodes import storyboard as sb
    monkeypatch.setattr(sb, "translate_to_en", _fake_translate)

    out = await sb.canvas_storyboarder_node(_canvas_state(lock_first_frame=False))

    shots = out["storyboard"]
    assert [s["mode"] for s in shots] == ["reference", "reference"]
    assert "first_frame" not in shots[0], "不锁首帧时不该留 first_frame 字段"


@pytest.mark.asyncio
async def test_canvas_storyboarder_chain_uses_next_first_frame_as_last(monkeypatch):
    """段间衔接：本段尾帧 = 下一段首帧；末段没有下一段，不该有尾帧。"""
    from app.nodes import storyboard as sb
    monkeypatch.setattr(sb, "translate_to_en", _fake_translate)

    out = await sb.canvas_storyboarder_node(
        _canvas_state(lock_first_frame=True, chain_frames=True))

    shots = out["storyboard"]
    assert shots[0]["last_frame"] == "http://mock/img/b.png"
    assert "last_frame" not in shots[1]


@pytest.mark.asyncio
async def test_canvas_storyboarder_chain_needs_lock(monkeypatch):
    """不锁首帧时没有 keyframe，chain 无从谈起 —— 不能留下非法的 last_frame。"""
    from app.nodes import storyboard as sb
    monkeypatch.setattr(sb, "translate_to_en", _fake_translate)

    out = await sb.canvas_storyboarder_node(
        _canvas_state(lock_first_frame=False, chain_frames=True))

    assert "last_frame" not in out["storyboard"][0]


# ---------------------------------------------------------------- 出图节点：锁定开关

@pytest.mark.asyncio
async def test_image_generator_lock_off_keeps_reference_mode(monkeypatch):
    """关掉首帧锁定 → 生成的首帧图只作参考图（旧行为），便于 A/B 对照。"""
    from app.nodes import image as image_mod

    class _Gw:
        async def generate_image(self, prompt, model=None, session_id=None,
                                 size=None, ratio=None, seed=None):
            return ["http://mock/gen.png"]

    monkeypatch.setattr(image_mod, "gateway", _Gw())
    state = {
        "session_id": "s-img",
        "user_id": "t",
        "raw_prompt": "p",
        "gen_type": "text_video",
        "lock_first_frame": False,
        "storyboard": [{"shot_id": 1, "prompt_en": "a cat", "mode": "text",
                        "seconds": "5", "aspect_ratio": "16:9", "reference_images": []}],
        "trace": [],
    }

    out = await image_mod.image_generator_node(state)

    shot = out["storyboard"][0]
    assert shot["mode"] == "reference"
    assert "first_frame" not in shot


@pytest.mark.asyncio
async def test_image_generator_passes_shot_ratio_to_api(monkeypatch):
    """出图画幅必须按镜传下去（画布节点 ratio / 分镜 aspect_ratio）。"""
    from app.nodes import image as image_mod

    seen: list[str | None] = []

    class _Gw:
        async def generate_image(self, prompt, model=None, session_id=None,
                                 size=None, ratio=None, seed=None):
            seen.append(ratio)
            return ["http://mock/gen.png"]

    monkeypatch.setattr(image_mod, "gateway", _Gw())
    state = {
        "session_id": "s-img2",
        "user_id": "t",
        "raw_prompt": "p",
        "gen_type": "text_video",
        "storyboard": [{"shot_id": 1, "prompt_en": "a cat", "mode": "text",
                        "seconds": "5", "aspect_ratio": "9:16", "reference_images": []}],
        "trace": [],
    }

    await image_mod.image_generator_node(state)

    assert seen == ["9:16"], f"出图请求没带上画幅: {seen!r}"
