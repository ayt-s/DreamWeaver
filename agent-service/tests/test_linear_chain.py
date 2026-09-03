"""Phase 1 冒烟测试：mock 网关跑通完整 LangGraph 线性链路。（Phase 3 独立轮询版）

不触真实 Agnes API（无 Key 也可运行），验证：
1. 图能构建并执行全链路
2. §3.4 契约：节点收 video_url 进 state、审计 trace 齐全
3. requirement_parser → script_writer → storyboarder → video_generator 顺序正确
"""
import asyncio

import pytest

from app.state import CreativeSessionState, TaskStatus
from app import graph


class _FakePoller:
    """测试用 poller 替身：get_future 直接返回已完成 future，不轮询。

    注意：video_generator 节点只依赖 poller 解决 future（Phase 3 架构），
    FakePoller 必须返回已完成的 future，否则 gather 会永久等待。
    """

    def __init__(self):
        self.pending_tasks: dict = {}

    async def start(self):
        pass

    async def stop(self):
        pass

    async def submit(self, video_id, model_name, session_id, shot_index):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({
            "video_url": f"http://mock/minio/shot_{shot_index}.mp4",
            "video_id": video_id,
        })
        return fut

    def get_future(self, video_id):
        # 测试中 poller 不预注册，直接创建并设置结果的 future
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({
            "video_url": f"http://mock/minio/shot_{video_id}.mp4",
            "video_id": video_id,
        })
        return fut


class MockGateway:
    """替换真实网关：文本返回固定 JSON，视频提交后立刻完成。"""

    async def chat(self, prompt, model=None, temperature=0.0, max_tokens=4096) -> str:
        if "解析为结构化 Brief" in prompt:
            return '{"theme": "产品宣传", "style": "科技感", "duration_seconds": "5", "audience": "年轻用户", "mood": "酷炫"}'
        if "Translate the following" in prompt:
            return "A futuristic product showcase, slow camera push-in, neon lighting"
        return '[{"shot_id": 1, "visual": "产品特写旋转", "camera": "推", "duration": 3, "style_note": "霓虹灯光"}, {"shot_id": 2, "visual": "产品场景切换", "camera": "移", "duration": 2, "style_note": "冷色调"}]'

    async def submit_video(self, prompt, model=None, seconds=None,
                           aspect_ratio=None, mode="text", reference_images=None) -> dict:
        return {"video_id": "video_mock_001", "model_name": "agnes-video-2.5-flash"}

    async def query_video(self, video_id, model_name, mode="text") -> dict:
        return {"status": "completed", "video_url": "http://mock/minio/shot.mp4"}

    async def generate_image(self, prompt, model=None) -> list[str]:
        return ["http://mock/image/generated.png"]

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def patch_gateway(monkeypatch):
    from app import gateway as gateway_mod
    from app.tools import video as video_mod
    from app.nodes import parser as parser_mod
    from app.nodes import script as script_mod
    from app.nodes import storyboard as storyboard_mod
    from app.nodes import video as nodes_video_mod
    from app.nodes import image as image_mod
    from app import poller as poller_mod

    monkeypatch.setattr(gateway_mod, "agnes", MockGateway())
    monkeypatch.setattr(parser_mod, "gateway", MockGateway())
    monkeypatch.setattr(script_mod, "gateway", MockGateway())
    monkeypatch.setattr(storyboard_mod, "gateway", MockGateway())
    monkeypatch.setattr(video_mod, "gateway", MockGateway())
    monkeypatch.setattr(image_mod, "gateway", MockGateway())
    monkeypatch.setattr(nodes_video_mod, "gateway", MockGateway())
    # nodes/video.py 与 tools/video.py 在 import 时已通过
    # `from app.poller import poller` 早绑定单例引用，必须逐个模块替换，
    # 否则节点会拿到真实 VideoPoller（其 future 无人解决 → 测试永久挂起）
    monkeypatch.setattr(video_mod, "poller", _FakePoller())
    monkeypatch.setattr(nodes_video_mod, "poller", _FakePoller())
    monkeypatch.setattr(poller_mod, "poller", _FakePoller())


@pytest.mark.asyncio
async def test_linear_chain_end_to_end():
    state: CreativeSessionState = {
        "session_id": "test-001",
        "user_id": "tester",
        "raw_prompt": "做一个奶粉广告视频",
        "status": TaskStatus.PENDING,
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
        "trace": [],
        "created_at": 0,
        "updated_at": 0,
    }

    config = {"configurable": {"thread_id": "test-001"}}
    result = await graph.compiled_graph.ainvoke(state, config=config)

    # 1. 全链路走完 → status 到 QC_CHECKING（Phase 2 新增 QC 节点）
    assert result["status"] == TaskStatus.QC_CHECKING

    # 2. brief / script / storyboard 逐层产出
    assert result["brief"]["theme"] == "产品宣传"
    assert len(result["script"]) == 2
    assert len(result["storyboard"]) == 2
    assert result["storyboard"][0]["prompt_en"].startswith("A futuristic")

    # 3. 图像生成节点产出 image_urls（mock 返回单张图）
    assert result["image_urls"] == ["http://mock/image/generated.png"]

    # 4. §3.4 契约：video_urls 与 storyboard 一一对应（工具只返回单个 URL，节点负责聚合）
    assert len(result["video_urls"]) == 2
    assert all(u.startswith("http://mock/") for u in result["video_urls"])

    # 5. 审计 trace：generate_image + 每镜一条 generate_video 记录
    tool_audits = [t for t in result["trace"] if t["tool_name"] == "generate_video"]
    assert len(tool_audits) == 2
    assert tool_audits[0]["result"]["video_url"] == result["video_urls"][0]
    assert tool_audits[0]["params"]["shot_index"] == 0
    assert tool_audits[1]["params"]["shot_index"] == 1

    image_audits = [t for t in result["trace"] if t["tool_name"] == "generate_image"]
    assert len(image_audits) == 1
    assert image_audits[0]["result"]["image_urls"] == result["image_urls"]