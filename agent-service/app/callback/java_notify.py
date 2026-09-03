"""FastAPI → Java Spring Boot 回调通知。

Phase 2 新增：视频生成完成/失败后，通知 Java 更新任务状态。
避免 Java 侧轮询，实现真正的异步解耦。
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def notify_java_completion(
    video_id: str,
    session_id: str,
    shot_index: int | None,
    status: str,
    video_url: str | None = None,
    error_message: str | None = None,
) -> None:
    """通知 Java 视频生成结果。

    Args:
        video_id: Agnes 返回的视频任务 ID
        session_id: LangGraph 会话 ID
        shot_index: 镜次索引（单镜任务为 None）
        status: completed / failed
        video_url: 成功时返回的视频 URL
        error_message: 失败时的错误信息
    """
    if not settings.java_notify_url:
        logger.debug("JAVA_NOTIFY_URL 未配置，跳过 Java 回调通知")
        return

    payload = {
        "video_id": video_id,
        "session_id": session_id,
        "shot_index": shot_index,
        "status": status,
    }
    if video_url:
        payload["video_url"] = video_url
    if error_message:
        payload["error_message"] = error_message

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.java_notify_url}/internal/notify",
                json=payload,
                timeout=10.0,
            )
            if resp.status_code == 200:
                logger.info("Java 回调通知成功: video_id=%s, status=%s", video_id, status)
            else:
                logger.warning(
                    "Java 回调通知失败: HTTP %d, body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
    except httpx.HTTPError as e:
        logger.error("Java 回调通知异常: %s", e)
