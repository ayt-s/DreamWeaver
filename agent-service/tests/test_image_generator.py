"""Phase 4 P0 图像生成节点测试：逐镜生图 + 回填 reference_images + mode 切换。"""
import pytest

from app.nodes.image import image_generator_node
from app.state import CreativeSessionState, TaskStatus


class FakeGateway:
    """fake 网关：按 prompt 返回固定 URL。"""

    async def generate_image(self, prompt, model=None) -> list[str]:
        return [f"http://mock/image/{prompt[:16]}.png"]


@pytest.fixture(autouse=True)
def patch_gateway(monkeypatch):
    from app.nodes import image as image_mod
    monkeypatch.setattr(image_mod, "gateway", FakeGateway())


@pytest.mark.asyncio
async def test_image_generator_per_shot():
    """测试逐镜生图，并回填 reference_images 和 mode。"""
    state: CreativeSessionState = {
        "session_id": "img-test-001",
        "user_id": "tester",
        "raw_prompt": "一只猫咪在阳光下",
        "status": TaskStatus.STORYBOARD_WRITING,
        "storyboard": [
            {"shot_id": 1, "prompt_en": "A cute cat in sunlight", "mode": "text",
             "seconds": "5", "aspect_ratio": "16:9", "reference_images": [],
             "cn_description": "猫咪特写"},
            {"shot_id": 2, "prompt_en": "Cat jumping", "mode": "text",
             "seconds": "4", "aspect_ratio": "16:9", "reference_images": [],
             "cn_description": "猫咪跳跃"},
        ],
        "trace": [],
    }

    result = await image_generator_node(state)

    # 生成 2 张图
    assert len(result["image_urls"]) == 2
    assert all(u.startswith("http://mock/image/") for u in result["image_urls"])

    # storyboard 被回填
    assert result["storyboard"][0]["reference_images"] == [result["image_urls"][0]]
    assert result["storyboard"][0]["mode"] == "image"
    assert result["storyboard"][1]["reference_images"] == [result["image_urls"][1]]
    assert result["storyboard"][1]["mode"] == "image"

    # 状态更新
    assert result["status"] == TaskStatus.ASSET_GENERATING

    # 审计 trace
    trace = result["trace"]
    assert len(trace) == 2
    for t in trace:
        assert t["tool_name"] == "generate_image"
        assert t["latency_ms"] >= 0
