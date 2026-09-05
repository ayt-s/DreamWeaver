"""FastAPI → Java Spring Boot 回调通知。

视频生成完成/失败后，通知 Java 更新任务状态。
避免 Java 侧轮询，实现真正的异步解耦。

2026-09 修复：回调契约改为按 session_id 关联（Java 侧无 video_id 列），
整会话一次回调携带全量 URL 数组，避免多镜逐条回调被终态检查丢弃。

2026-09 加固：
- 回调带 3 次重试（1s / 3s / 5s 退避），应对 Java 短暂重启/网络抖动
- 仍失败则写入本地 fallback（data/fallback.jsonl），等 Java 起来后
  调 /v1/internal/sync-fallback 拉走并落库，Java 侧启动时会自动触发一次
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 回调重试配置：最多 3 次尝试，间隔 1s / 3s / 5s（含首次）
_RETRY_DELAYS = (1.0, 3.0, 5.0)


async def notify_java_completion(
    session_id: str,
    status: str,
    video_id: str = "",
    shot_index: int | None = None,
    video_url: str | None = None,
    video_urls: list[str] | None = None,
    error_message: str | None = None,
    image_urls: list[str] | None = None,
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
    if image_urls is not None:
        payload["image_urls"] = image_urls

    last_err: Exception | None = None
    for i, delay in enumerate(_RETRY_DELAYS):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{settings.java_notify_url}/internal/notify",
                    json=payload,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    logger.info("Java 回调通知成功: session=%s, status=%s, urls=%d (尝试 %d)",
                                session_id, status, len(payload["video_urls"]), i + 1)
                    return
                # 5xx / 408 值得重试；4xx（除 429）是契约问题不该重试
                if resp.status_code >= 500 or resp.status_code in (408, 429):
                    logger.warning("Java 回调 HTTP %d，重试 (%d/%d)",
                                   resp.status_code, i + 1, len(_RETRY_DELAYS))
                    last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    logger.warning("Java 回调 HTTP %d（不重试），body=%s",
                                   resp.status_code, resp.text[:200])
                    return
        except httpx.HTTPError as e:
            last_err = e
            logger.warning("Java 回调异常: %s，重试 (%d/%d)", e, i + 1, len(_RETRY_DELAYS))

        # 最后一次也失败：写 fallback 兜底
        if i == len(_RETRY_DELAYS) - 1:
            break

        import asyncio
        await asyncio.sleep(delay)

    # 全部重试失败 → 写本地 fallback，等 Java 起来后拉走
    from app import fallback
    payload_with_attempts = dict(payload)
    payload_with_attempts["attempts"] = len(_RETRY_DELAYS)
    try:
        fid = await fallback.append(payload_with_attempts, str(last_err or "未知错误"))
        logger.error("Java 回调全部重试失败，已写本地 fallback: id=%s session=%s",
                     fid, session_id)
    except Exception as e:
        logger.error("写入本地 fallback 也失败，数据可能丢失: session=%s err=%s",
                     session_id, e)