"""段重生回归测试：视频/图片段重生的索引对齐与顺序。

覆盖两个已修缺陷：
1. video_generator_node 混合重生（复用段 + 新生段交错）时 video_urls 顺序错乱。
2. image_generator_node 段重生时失败段被 continue 跳过，导致 image_urls 长度
   小于 segments 长度、索引错位。

不触真实 Agnes API：generate_video_tool / poller / gateway 全部 mock。
"""
import asyncio

import pytest

from app.state import CreativeSessionState, TaskStatus


# --------------------------------------------------------------------------
# 视频节点：混合重生顺序
# --------------------------------------------------------------------------

class _FakePoller:
    """future 直接已完成，不轮询；URL 由 video_id 推导便于断言顺序。"""

    def get_future(self, video_id):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({
            "video_url": f"http://mock/new/{video_id}.mp4",
            "video_id": video_id,
        })
        return fut


def _make_video_state(storyboard, video_urls=None, video_ids=None):
    state: CreativeSessionState = {
        "session_id": "video-rework-test",
        "user_id": "tester",
        "raw_prompt": "混合重生",
        "gen_type": "image_video",
        "status": TaskStatus.VIDEO_GENERATING,
        "storyboard": storyboard,
        "trace": [],
    }
    if video_urls is not None:
        state["video_urls"] = video_urls
    if video_ids is not None:
        state["video_ids"] = video_ids
    return state


def _shot(idx: int, existing: str = "") -> dict:
    shot = {
        "shot_id": idx + 1,
        "prompt_en": f"prompt-{idx}",
        "seconds": "5",
        "aspect_ratio": "16:9",
        "mode": "text",
        "reference_images": [],
    }
    if existing:
        shot["existing_video_url"] = existing
    return shot


@pytest.fixture
def patch_video(monkeypatch):
    """mock 掉 generate_video_tool 与 poller（video.py 顶部 import 了 gateway 供 fixture 用）。"""
    from app.nodes import video as video_mod

    async def _fake_generate_video_tool(prompt, seconds, mode, aspect_ratio,
                                        reference_images, session_id,
                                        shot_index, model=None,
                                        first_frame=None, last_frame=None) -> dict:
        return {"video_id": f"vid{shot_index}", "status": "pending"}

    monkeypatch.setattr(video_mod, "generate_video_tool", _fake_generate_video_tool)
    monkeypatch.setattr(video_mod, "poller", _FakePoller())


@pytest.mark.asyncio
async def test_video_rework_mixed_order(patch_video):
    """storyboard = [复用, 新生, 复用] → video_urls 顺序必须是 [复用0, 新生1, 复用2]。"""
    from app.nodes.video import video_generator_node

    state = _make_video_state([
        _shot(0, existing="http://mock/old/reuse0.mp4"),
        _shot(1),
        _shot(2, existing="http://mock/old/reuse2.mp4"),
    ])

    result = await video_generator_node(state)

    # 顺序严格按段索引，而不是「先复用后新生」
    assert result["video_urls"] == [
        "http://mock/old/reuse0.mp4",
        "http://mock/new/vid1.mp4",
        "http://mock/old/reuse2.mp4",
    ]
    # video_ids 同步对齐
    assert result["video_ids"] == ["reused-0", "vid1", "reused-2"]


@pytest.mark.asyncio
async def test_video_rework_resume_keeps_existing_prefix(patch_video):
    """断点恢复：已有 video_urls[0] 应保留在索引 0，新生的段落位到索引 1。"""
    from app.nodes.video import video_generator_node

    state = _make_video_state(
        [_shot(0), _shot(1)],
        video_urls=["http://mock/old/done0.mp4"],
        video_ids=["old-vid0"],
    )

    result = await video_generator_node(state)

    assert result["video_urls"] == [
        "http://mock/old/done0.mp4",
        "http://mock/new/vid1.mp4",
    ]
    assert result["video_ids"] == ["old-vid0", "vid1"]


# --------------------------------------------------------------------------
# 图片节点：段重生索引对齐
# --------------------------------------------------------------------------

class _FailingGateway:
    """含 'fail' 的 prompt 返回空列表（模拟生成失败）。"""

    async def generate_image(self, prompt, model=None, size=None, ratio=None, seed=None) -> list[str]:
        if "fail" in prompt:
            return []
        return [f"http://mock/image/{prompt}.png"]


@pytest.mark.asyncio
async def test_image_rework_index_alignment_on_failure(monkeypatch):
    """3 段、中间段生成失败 → image_urls 长度仍为 3，失败位为空串。"""
    from app.nodes import image as image_mod
    from app.nodes.image import image_generator_node

    monkeypatch.setattr(image_mod, "gateway", _FailingGateway())

    state: CreativeSessionState = {
        "session_id": "image-rework-test",
        "user_id": "tester",
        "raw_prompt": "段重生索引对齐",
        "gen_type": "text_image",
        "status": TaskStatus.ASSET_GENERATING,
        "segments": [
            {"prompt": "seg-a", "existing_image_url": ""},
            {"prompt": "seg-fail-b", "existing_image_url": ""},
            {"prompt": "seg-c", "existing_image_url": ""},
        ],
        "trace": [],
    }

    result = await image_generator_node(state)

    # 每段一个元素，失败段空串占位（与 segments 索引严格对齐）
    assert len(result["image_urls"]) == len(state["segments"]) == 3
    assert result["image_urls"][0] == "http://mock/image/seg-a.png"
    assert result["image_urls"][1] == ""
    assert result["image_urls"][2] == "http://mock/image/seg-c.png"

    # storyboard 的 image_url 与落库列表逐段一致
    assert [s["image_url"] for s in result["storyboard"]] == result["image_urls"]

    # 有非空 URL → 判定为完成
    assert result["status"] == TaskStatus.COMPLETED
