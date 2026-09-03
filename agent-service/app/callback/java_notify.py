"""FastAPI → Java Spring Boot 回调通知。

视频生成完成/失败后，通知 Java 更新任务状态。
避免 Java 侧轮询，实现真正的异步解耦。

2026-09 修复：回调契约改为按 session_id 关联（Java 侧无 video_id 列），
整会话一次回调携带全量 URL 数组，避免多镜逐条回调被终态检查丢弃。
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def notify_java_completion(
    session_id: str,
    status: str,
    video_id: str = "",
    shot_index: int | None = None,
    video_url: str | None = None,
    video_urls: list[str] | None = None,
    error_message: str | None = None,
) -> None:
    """通知 Java 视频生成结果。

    Args:
        session_id: LangGraph 会话 ID（Java 侧关联主键）
        status: completed / failed
        video_id: Agnes 返回的视频任务 ID（审计用，Java 不按此查任务）
        shot_index: 镜次索引（整会话回调为 None）
        video_url: 单值兼容字段（已弃用，保留兼容）
        video_urls: 全量视频 URL 数组（主载荷）
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
        "video_urls": video_urls or ([video_url] if video_url else []),
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
                logger.info("Java 回调通知成功: session=%s, status=%s, urls=%d",
                            session_id, status, len(payload["video_urls"]))
            else:
                logger.warning(
                    "Java 回调通知失败: HTTP %d, body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
    except httpx.HTTPError as e:
        logger.error("Java 回调通知异常: %s", e)