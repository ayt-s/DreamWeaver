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


def _to_int_seconds(raw) -> int | None:
    """把分镜/段配置里的 seconds 解析成正整数秒；不可判定返回 None。

    容忍 storyboard 里的字符串形式（`storyboard.py` 写的是 `str(seconds)`）、
    float（`image_slideshow` 的 slide_seconds）与脏值（"5s" / "" / None）。
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(str(raw).strip().rstrip("sS"))
    except (TypeError, ValueError):
        return None
    if value <= 0 or value != value:  # NaN 也被 != 拦下
        return None
    return int(round(value))


def total_shot_seconds(storyboard: list | None,
                       segments: list | None = None) -> int | None:
    """汇总本次会话**实际提交生成**的总秒数（#33 的配额口径）。

    ★ 2026-09-19 新增（#33）：Java 的 `api_quota.used_seconds` 靠回调里的
      `shot_seconds` 累加，而 agent 从来没发过它 → 每条回调都按 Java 的
      `DEFAULT_SHOT_SECONDS=5` 记账，配额与实际生成时长无关。

    口径（与「按段计费」的语义对齐）：
      - 逐镜取 `seconds`，取不到时用 `settings.default_seconds`（提交时网关也用这个兜底）；
      - `segments` 非空时以 **segments** 为准（画布模式每段独立提交，storyboard
        可能是由 segments 重建的，但段里才带真实秒数）；
      - 全都取不到 / 列表为空 → 返回 **None**（= 不发字段，保留 Java 默认行为）。
        **刻意不返回 0**：0 会把配额页写成「消耗 0 秒」，比默认 5 秒更错。
    """
    items = segments if segments else storyboard
    if not isinstance(items, list) or not items:
        return None
    fallback = _to_int_seconds(getattr(settings, "default_seconds", None)) or 5
    total = 0
    seen = False
    for item in items:
        if not isinstance(item, dict):
            continue
        secs = _to_int_seconds(item.get("seconds"))
        if secs is None:
            secs = fallback
        total += secs
        seen = True
    return total if seen else None


async def notify_java_completion(
    session_id: str,
    status: str,
    video_id: str = "",
    shot_index: int | None = None,
    video_url: str | None = None,
    video_urls: list[str] | None = None,
    error_message: str | None = None,
    image_urls: list[str] | None = None,
    storyboard: str | None = None,
    shot_seconds: int | None = None,
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
        shot_seconds: **本次生成消耗的总秒数**（#33）。
            Java 的 `NotifyRequest.shot_seconds`（Integer，单位秒）声明了它、
            `NotifyServiceImpl` 也真的拿它去 `apiQuotaMapper.increment(...)`
            （`shot_seconds != null ? 它 : DEFAULT_SHOT_SECONDS=5`），
            但 agent 侧**从来没发过这个字段** —— 于是每条回调都按默认 5 秒记账。
            后果：`api_quota.used_seconds` 与实际生成时长无关。实测量级：
            6 段 × 5 秒的任务，真实 30 秒只记 5 秒（少 6 倍）；反过来 1 段 12 秒
            只记 5 秒（少一半多）。配额页因此完全不可信。
            填法见 `total_shot_seconds()`；**None = 保持 Java 的默认行为**，
            刻意不发（例如失败回调、纯图片回调），避免把「不知道」写成 0。
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
    if storyboard is not None:
        payload["storyboard"] = storyboard
    # ★ 2026-09-19 修（#33）：把真实消耗秒数发给 Java（字段名以 Java 的
    #   NotifyRequest.shot_seconds 为准）。None 时**不发**这个键 ——
    #   发了 null 会被 Jackson 解析成 null 与「不发」等价，但显式省略更清楚。
    if shot_seconds is not None:
        payload["shot_seconds"] = int(shot_seconds)

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


async def probe_session_tracked(session_id: str) -> bool | None:
    """问 Java：这个 session_id 是否还属于一个「非终态」任务？

    用途：启动恢复前先确认会话仍被认领。用户若已把任务「全量重生」（换了新
    session_id）或删除，原会话继续跑只会白烧 agnes 额度，且结果回调会因
    session_id 不匹配被 Java 丢弃。复用 /internal/heartbeat 端点（它顺带续期看门狗）。

    Returns:
        True  = 仍被认领（非终态任务存在）
        False = 明确无人认领（任务查不到 / 已是终态 / session_id 为空）
        None  = 无法判定（未配置、Java 不可达、响应非法）——调用方必须保守处理
    """
    if not session_id or not settings.java_notify_url:
        return None
    url = f"{settings.java_notify_url.rstrip('/')}/internal/heartbeat"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(url, json={"session_id": session_id})
        if resp.status_code != 200:
            return None
        body = resp.json()
        data = (body or {}).get("data")
        if not isinstance(data, dict) or "tracked" not in data:
            return None
        return bool(data["tracked"])
    except Exception as exc:
        logger.debug("probe_session_tracked(%s) 无法判定: %s", session_id, exc)
        return None


async def renew_watchdog(session_id: str) -> bool | None:
    """★ 2026-09-19 新增（#6/#19）：**只续期看门狗，不依赖任何状态转移**。

    为什么需要单独一只（而不是复用 `notify_java_completion(status="queued")`）：

    Java `NotifyServiceImpl.TRANSITION_TABLE`（web-backend，2026-09-19 现读）是
      queued          → {completed, failed, interrupted}
      video_generating→ {completed, failed}
      interrupted     → {completed, failed, queued}
    恢复流程原来只发 `status=queued` 想让 Java「重新武装看门狗」。可是
    **queued → queued 不在表里**（Java 侧任务卡在 queued 是 Phase 1 的常态：
    agent 从不发 video_generating），于是这条回退报到被 `非法状态跳转` 丢掉，
    `stuckTaskWatchdog.watch(...)` 那一行（只在合法转移后执行）也没跑到。
    最坏情形：Redis 里的看门狗 TTL 条目已过期 + 会话刚被恢复、还排在调度队列里
    没开始跑（心跳协程由 `_run_session` 启动，排队期间不存在）→ 整段时间
    任务既无 TTL 也无心跳，彻底无兜底。

    本函数走 `/internal/heartbeat`：Java 侧 `handleHeartbeat` 对**非终态**任务
    无条件 `stuckTaskWatchdog.watch(taskId, genType)` —— 与 from 状态无关，
    因此对 queued / interrupted / video_generating 都成立。

    Returns: 同 `probe_session_tracked`（True/False/None），供调用方记日志。
    """
    return await probe_session_tracked(session_id)