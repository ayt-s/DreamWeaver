"""Phase 1 冒烟测试：mock 网关跑通完整 LangGraph 线性链路。（Phase 4 P0 文生图版）

不触真实 Agnes API（无 Key 也可运行），验证：
1. 图能构建并执行全链路
2. 文生图 + 图生视频贯通
3. requirement_parser → script_writer → storyboarder → image_generator → video_generator 顺序正确
4. novel_image 模式：只出图不出视频
"""
import asyncio

import pytest

from app.state import CreativeSessionState, TaskStatus
from app import graph


class _FakePoller:
    """测试用 poller 替身：get_future 直接返回已完成 future，不轮询。"""

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
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({
            "video_url": f"http://mock/minio/shot_{video_id}.mp4",
            "video_id": video_id,
        })
        return fut


class MockGateway:
    """替换真实网关：文本返回固定 JSON，图像/视频同步完成。"""

    async def chat(self, prompt, model=None, temperature=0.0, max_tokens=4096) -> str:
        if "解析为结构化 Brief" in prompt:
            return '{"theme": "产品宣传", "style": "科技感", "duration_seconds": "5", "audience": "年轻用户", "mood": "酷炫"}'
        if "Translate the following" in prompt:
            return "A futuristic product showcase, slow camera push-in, neon lighting"
        return '[{"shot_id": 1, "visual": "产品特写旋转", "camera": "推", "duration": 3, "style_note": "霓虹灯光"}, {"shot_id": 2, "visual": "产品场景切换", "camera": "移", "duration": 2, "style_note": "冷色调"}]'

    async def generate_image(self, prompt, model=None) -> list[str]:
        return [f"http://mock/image/img_{prompt[:8]}.png"]

    async def submit_video(self, prompt, model=None, seconds=None,
                           aspect_ratio=None, mode="text", reference_images=None) -> dict:
        return {"video_id": f"video_{mode}_{len(reference_images or [])}", "model_name": "agnes-video-2.5-flash"}

    async def query_video(self, video_id, model_name, mode="text") -> dict:
        return {"status": "completed", "video_url": "http://mock/minio/shot.mp4"}

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def patch_gateway(monkeypatch):
    from app import gateway as gateway_mod
    from app.tools import video as video_mod
    from app.nodes import parser as parser_mod
    from app.nodes import script as script_mod
    from app.nodes import storyboard as storyboard_mod
    from app.nodes import image as image_mod
    from app.nodes import video as nodes_video_mod
    from app import poller as poller_mod

    monkeypatch.setattr(gateway_mod, "agnes", MockGateway())
    monkeypatch.setattr(parser_mod, "gateway", MockGateway())
    monkeypatch.setattr(script_mod, "gateway", MockGateway())
    monkeypatch.setattr(storyboard_mod, "gateway", MockGateway())
    monkeypatch.setattr(image_mod, "gateway", MockGateway())
    monkeypatch.setattr(video_mod, "gateway", MockGateway())
    monkeypatch.setattr(nodes_video_mod, "gateway", MockGateway())
    # poller 引用必须逐个模块替换
    monkeypatch.setattr(video_mod, "poller", _FakePoller())
    monkeypatch.setattr(nodes_video_mod, "poller", _FakePoller())
    monkeypatch.setattr(poller_mod, "poller", _FakePoller())


@pytest.mark.asyncio
async def test_linear_chain_with_image_gen():
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

    # 1. 全链路走完 → status 到 QC_CHECKING
    assert result["status"] == TaskStatus.QC_CHECKING

    # 2. brief / script / storyboard 逐层产出
    assert result["brief"]["theme"] == "产品宣传"
    assert len(result["script"]) == 2
    assert len(result["storyboard"]) == 2
    assert result["storyboard"][0]["prompt_en"].startswith("A futuristic")

    # 3. 图像生成节点产出 image_urls
    assert len(result["image_urls"]) == 2
    assert all(u.startswith("http://mock/image/") for u in result["image_urls"])

    # 4. 图生视频：每个 shot 有 reference_images，mode 改为 "image"
    assert result["storyboard"][0]["reference_images"] == [result["image_urls"][0]]
    assert result["storyboard"][0]["mode"] == "image"
    assert result["storyboard"][1]["reference_images"] == [result["image_urls"][1]]
    assert result["storyboard"][1]["mode"] == "image"

    # 5. video_urls 与 storyboard 一一对应
    assert len(result["video_urls"]) == 2
    assert all(u.startswith("http://mock/") for u in result["video_urls"])

    # 6. 审计 trace：generate_image + generate_video 记录
    image_audits = [t for t in result["trace"] if t["tool_name"] == "generate_image"]
    video_audits = [t for t in result["trace"] if t["tool_name"] == "generate_video"]
    assert len(image_audits) == 2
    assert len(video_audits) == 2

    # 7. 检查 video audit 包含正确的参数
    for va in video_audits:
        assert va["params"]["shot_index"] in [0, 1]
        assert va["params"]["seconds"] in ["5", "4"]


@pytest.mark.asyncio
async def test_novel_image_only_no_video():
    """小说转图模式：只出图不出视频，image_generator 后直达 END。"""
    state: CreativeSessionState = {
        "session_id": "test-novel",
        "user_id": "tester",
        "raw_prompt": "第一章：少年在雨夜救下一只白狐",
        "gen_type": "novel_image",
        "status": TaskStatus.PENDING,
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
        "trace": [],
        "created_at": 0,
        "updated_at": 0,
    }

    config = {"configurable": {"thread_id": "test-novel"}}
    result = await graph.compiled_graph.ainvoke(state, config=config)

    # 1. 终点是 image_generator（asset_generating），不是 QC_CHECKING
    assert result["status"] == TaskStatus.ASSET_GENERATING

    # 2. 有图无视频
    assert len(result["image_urls"]) == 2
    assert all(u.startswith("http://mock/image/") for u in result["image_urls"])
    assert not result.get("video_urls")

    # 3. trace 里有 generate_image、绝无 generate_video
    image_audits = [t for t in result["trace"] if t["tool_name"] == "generate_image"]
    video_audits = [t for t in result["trace"] if t["tool_name"] == "generate_video"]
    assert len(image_audits) == 2
    assert len(video_audits) == 0