"""逐镜异常隔离回归测试（2026-09-19 修 #10 / #12）。

两个已修缺陷是同一类：**单镜失败清空整会话**。

1. `#10 video_generator_node`：`generate_video_tool` 裸调用，任一镜提交抛错
   （4xx 参数类 / 所有 provider 重试耗尽 / 网关异常）→ 异常冒泡到 main 的失败分支 →
   整会话 failed ⇒ 同会话其它**已生成/已付费**的图与视频全都不进 Java（额度照扣）。
   修法：每镜 try/except，失败记进 `submit_errors` → `error_msgs` → `state["video_error"]`
   （由 notify_final 随产物带回 Java），**继续后续镜**。
2. `#12 image_generator_node` 标准模式逐镜出图：没有 try/except（直出图路径本来就有，
   两条路径不对称）⇒ 一张图撞 429/504 耗尽重试 = 整会话 failed，其余镜已付费的图一起丢。
   修法：单镜 try/except，失败让该镜留空串占位（保索引对齐）继续。

不触真实 Agnes API：generate_video_tool / poller / gateway 全部 mock。
"""
import asyncio

import pytest

from app.state import CreativeSessionState, TaskStatus


# --------------------------------------------------------------------------
# #10 视频节点：一镜提交失败，其余镜照常提交并回传
# --------------------------------------------------------------------------

class _FakePoller:
    """future 直接已完成，不轮询；URL 由 video_id 推导便于断言。"""

    def get_future(self, video_id):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({
            "video_url": f"http://mock/new/{video_id}.mp4",
            "video_id": video_id,
        })
        return fut


def _shot(idx: int) -> dict:
    return {
        "shot_id": idx + 1,
        "prompt_en": f"prompt-{idx}",
        "seconds": "5",
        "aspect_ratio": "16:9",
        "mode": "text",
        "reference_images": [],
    }


def _video_state(shots: list[dict]) -> CreativeSessionState:
    return {
        "session_id": "isolation-test-video",
        "user_id": "tester",
        "raw_prompt": "逐镜隔离",
        "gen_type": "image_video",
        "status": TaskStatus.VIDEO_GENERATING,
        "storyboard": shots,
        "trace": [],
    }


@pytest.mark.asyncio
async def test_submit_failure_of_one_shot_does_not_abort_session(monkeypatch):
    """第 2 段提交抛错 → 会话不失败，第 1/3 段照常出产物，错误进 video_error。"""
    from app.nodes import video as video_mod

    async def _fake_generate_video_tool(prompt, seconds, mode, aspect_ratio,
                                        reference_images, session_id,
                                        shot_index, model=None,
                                        first_frame=None, last_frame=None) -> dict:
        if shot_index == 1:
            raise RuntimeError("所有 provider 都失败: ReadTimeout")
        return {"video_id": f"vid{shot_index}", "status": "pending"}

    monkeypatch.setattr(video_mod, "generate_video_tool", _fake_generate_video_tool)
    monkeypatch.setattr(video_mod, "poller", _FakePoller())

    from app.nodes.video import video_generator_node

    result = await video_generator_node(_video_state([_shot(0), _shot(1), _shot(2)]))

    # 不抛异常本身就是要害：原来这里会把整个会话带成 failed
    assert result["video_urls"] == [
        "http://mock/new/vid0.mp4",
        "http://mock/new/vid2.mp4",
    ]
    assert result["video_ids"] == ["vid0", "vid2"]
    # 失败原因要能被 Java 看到（error_message），并且指明是哪一段
    assert "第 2 段提交失败" in result["video_error"]
    assert "ReadTimeout" in result["video_error"]
    # 仍在正常推进（不是 FAILED）——产物才有机会进 Java
    assert result["status"] == TaskStatus.VIDEO_GENERATING


# --------------------------------------------------------------------------
# #12 图像节点（标准模式）：一镜出图失败，其余镜照常出图并回传
# --------------------------------------------------------------------------

class _FlakyGateway:
    """第 1 镜抛 429；第 2 镜正常返回。"""

    async def generate_image(self, prompt, model=None, size=None, ratio=None, seed=None) -> list[str]:
        if "shot0" in prompt:
            raise RuntimeError("429 Too Many Requests（重试耗尽）")
        return [f"http://mock/image/{prompt[:16]}.png"]


@pytest.mark.asyncio
async def test_image_shot_failure_isolated(monkeypatch):
    """标准模式逐镜出图：一镜 429 不该把其余镜已付费的图一起清掉。"""
    from app.nodes import image as image_mod

    monkeypatch.setattr(image_mod, "gateway", _FlakyGateway())

    from app.nodes.image import image_generator_node

    state: CreativeSessionState = {
        "session_id": "isolation-test-image",
        "user_id": "tester",
        "raw_prompt": "逐镜隔离",
        "gen_type": "text_video",
        "status": TaskStatus.STORYBOARD_WRITING,
        "storyboard": [
            {"shot_id": 1, "prompt_en": "shot0 描述", "mode": "text", "seconds": "5",
             "aspect_ratio": "16:9", "reference_images": []},
            {"shot_id": 2, "prompt_en": "shot1 描述", "mode": "text", "seconds": "5",
             "aspect_ratio": "16:9", "reference_images": []},
        ],
        "trace": [],
    }

    result = await image_generator_node(state)

    # 失败镜留空串占位（索引对齐），成功镜的产物必须还在
    assert result["image_urls"][0] == ""
    assert result["image_urls"][1].startswith("http://mock/image/")
    # 失败镜的图不该回填给 video 节点当首帧
    assert state["storyboard"][0].get("reference_images", []) == []
    assert state["storyboard"][1]["reference_images"] == [result["image_urls"][1]]
