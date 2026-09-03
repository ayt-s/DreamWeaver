"""Phase 4 P0 图像生成节点测试。"""
import pytest

from app.nodes.image import image_generator_node
from app.state import CreativeSessionState, TaskStatus


class FakeGateway:
    """fake 网关：图像返回固定 URL。"""

    async def generate_image(self, prompt, model=None) -> list[str]:
        return [
            "http://mock/image/img1.png",
            "http://mock/image/img2.png",
        ]


@pytest.fixture(autouse=True)
def patch_gateway(monkeypatch):
    from app.nodes import image as image_mod
    monkeypatch.setattr(image_mod, "gateway", FakeGateway())


@pytest.mark.asyncio
async def test_image_generator_node():
    state: CreativeSessionState = {
        "session_id": "img-test-001",
        "user_id": "tester",
        "raw_prompt": "一只猫咪在阳光下",
        "status": TaskStatus.ASSET_GENERATING,
        "trace": [],
    }

    result = await image_generator_node(state)

    assert result["image_urls"] == ["http://mock/image/img1.png", "http://mock/image/img2.png"]
    assert result["status"] == TaskStatus.ASSET_GENERATING

    # 审计 trace 包含 generate_image 记录
    trace = result["trace"]
    assert len(trace) == 1
    assert trace[0]["tool_name"] == "generate_image"
    assert trace[0]["result"]["image_urls"] == result["image_urls"]
    assert trace[0]["params"]["prompt"] == "一只猫咪在阳光下"
    assert trace[0]["latency_ms"] >= 0
