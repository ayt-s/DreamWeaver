"""工具层：MCP 风格工具注册区（Phase 2 独立轮询重构版）。

契约：
- generate_video_tool：提交视频任务，后台 VideoPoller 异步轮询
- 返回 {video_id, status}；结果通过 poller.get_future(video_id) 获取
"""
import logging

from app.config import settings
from app.gateway.agnes import gateway
from app.poller import poller

logger = logging.getLogger(__name__)


async def generate_video_tool(prompt: str, seconds: str, mode: str,
                              aspect_ratio: str, reference_images: list,
                              session_id: str, shot_index: int,
                              model: str | None = None) -> dict:
    """提交视频任务，立即返回，由独立 VideoPoller 异步轮询。

    返回契约：{"video_id": str, "status": "submitted"}
    - 调用方通过 poller.get_future(video_id) 获取结果 Future
    """
    submitted = await gateway.submit_video(
        prompt=prompt,
        seconds=seconds,
        mode=mode,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images or [],
        model=model,
    )
    video_id = submitted["video_id"]
    model_name = submitted["model_name"]

    await poller.submit(
        video_id=video_id, model_name=model_name,
        session_id=session_id, shot_index=shot_index,
    )

    logger.info(
        "generate_video_tool 提交成功: video_id=%s shot=%d",
        video_id, shot_index,
    )
    return {"video_id": video_id, "status": "submitted"}
