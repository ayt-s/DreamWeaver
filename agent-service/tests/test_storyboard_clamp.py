"""视频时长钳制的回归测试（2026-09 联调实测：分镜 2-3s 短镜导致 Agnes 400）。"""
import pytest

from app.nodes.storyboard import MIN_SECONDS, MAX_SECONDS, storyboarder_node
from app.state import CreativeSessionState


class FakeGateway:
    """fake 网关：文本返回固定英文提示词。"""

    async def chat(self, prompt, model=None, temperature=0.0, max_tokens=4096) -> str:
        return "A red apple rotating, cinematic lighting"


@pytest.fixture(autouse=True)
def patch_gateway(monkeypatch):
    from app.nodes import storyboard as sb
    monkeypatch.setattr(sb, "gateway", FakeGateway())


@pytest.mark.asyncio
async def test_seconds_clamped_to_valid_range():
    """分镜时长 2s/99s 必须被钳到 [4, 12]——否则 Agnes 返回 400。"""
    state: CreativeSessionState = {
        "session_id": "t-1",
        "user_id": "u",
        "raw_prompt": "x",
        "script": [
            {"shot_id": 1, "visual": "apple", "camera": "push", "duration": 2, "style_note": "x"},   # → 4
            {"shot_id": 2, "visual": "apple", "camera": "static", "duration": 99, "style_note": "x"},  # → 12
            {"shot_id": 3, "visual": "apple", "camera": "pan", "duration": 6, "style_note": "x"},      # → 6
        ],
    }
    result = await storyboarder_node(state)
    seconds = [int(s["seconds"]) for s in result["storyboard"]]
    assert seconds == [MAX_SECONDS if d > MAX_SECONDS else MIN_SECONDS if d < MIN_SECONDS else d
                       for d in (2, 99, 6)]
    assert seconds == [4, 12, 6]
    assert all(MIN_SECONDS <= s <= MAX_SECONDS for s in seconds)