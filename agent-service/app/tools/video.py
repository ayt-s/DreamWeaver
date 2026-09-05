"""工具层：MCP 风格工具注册区（Phase 2 独立轮询重构版）。

契约：
- generate_video_tool：提交视频任务，后台 VideoPoller 异步轮询
- 返回 {video_id, status, provider}；结果通过 poller.get_future(video_id) 获取
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

    多 provider 场景：submit_video 内部按 session 粘性选 client，失败时切下一个
    provider 重试。返回 dict 额外带 provider 字段，poller 用它查询该视频，同时
    同步更新 session 粘附（避免后续 poll 用错账号）。

    返回契约：{"video_id": str, "status": "submitted", "provider": str}
    - 调用方通过 poller.get_future(video_id) 获取结果 Future
    """
    submitted = await gateway.submit_video(
        prompt=prompt,
        seconds=seconds,
        mode=mode,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images or [],
        model=model,
        session_id=session_id,
    )
    video_id = submitted["video_id"]
    model_name = submitted["model_name"]
    provider = submitted.get("provider", "intl")

    # 更新 session 粘附：submit 实际用的 provider 可能与 session 原粘附不同（failover），
    # 显式绑定保证后续 poll 用同一 provider
    gateway.bind_session(session_id, provider)

    await poller.submit(
        video_id=video_id, model_name=model_name,
        session_id=session_id, shot_index=shot_index,
        provider=provider,
    )

    logger.info(
        "generate_video_tool 提交成功: video_id=%s shot=%d provider=%s",
        video_id, shot_index, provider,
    )
    return {"video_id": video_id, "status": "submitted", "provider": provider}
