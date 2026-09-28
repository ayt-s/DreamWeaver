"""notify_final 节点：图的终态唯一一次完成回调（Task A9）。

**为什么需要它（A5 实测暴露）**

标准模式的完成回调原先在 `video_generator_node` 里发，**位置在 QC 之前**：

    video_generator ──回调 completed──▶ Java 任务转终态
          ↓
    asset_fetch → qc_checker → fix_looping → ...

于是产生两个后果，都会让 B 批次的自愈循环彻底失效：

1. `NotifyServiceImpl.handleCompletion` 的终态检查会**丢弃**后续回调
   （`completed`/`failed` 直接 return），所以 `fix_looping` 重生后的产物
   永远送不到 Java，`result_json` 停在第一版
2. `handleHeartbeat` 对已终态任务回 `tracked=false`，而 `main.py` 把
   `tracked=false` 当**中止信号** → `abort.mark(session_id)` → 自愈循环
   在第一轮重生前就被自己掐死

根因是顺序：QC（及其引出的修复循环）必须在「任务宣告终态」之前完成。
本节点把回调收敛到图的真正终态，保证**无论走哪条边都恰好发一次回调**
（否则任务会卡在 queued 直到看门狗兜底成 interrupted）。

**状态判定**

- `video_urls` 为空（全镜生成失败）→ `failed`
- QC 有失败镜（含 give_up）→ `completed` + `error_message` 说明未通过情况。
  **不标 failed**：分段视频仍可用，与 `synthesizer` 的降级哲学一致。
- QC 全通过 → `completed`，`error_message` 为空（清掉历史残留）
"""
import asyncio
import logging

from app.state import CreativeSessionState, TaskStatus

logger = logging.getLogger(__name__)


def summarize_qc_report(qc_report: dict | None) -> str:
    """把 qc_report 汇总成一句可读的失败说明。

    公开（非下划线）是因为 `synthesizer` 也要用：画布模式的回调由它发出，
    质检结论必须随那条回调一起回 Java —— 两处各写一份必然漂移。
    """
    if not isinstance(qc_report, dict):
        return ""
    failed = qc_report.get("failed_shots") or []
    if not failed:
        return ""
    total = qc_report.get("total_shots") or len(qc_report.get("shots") or [])
    shots = {s.get("index"): s for s in (qc_report.get("shots") or []) if isinstance(s, dict)}
    reasons = []
    for idx in failed[:3]:  # 最多列 3 条，避免 error_message 过长（Java 侧 512 上限）
        s = shots.get(idx) or {}
        why = str(s.get("error") or "").strip() or "未通过"
        reasons.append(f"第 {idx + 1} 镜：{why}")
    more = f" 等 {len(failed)} 镜" if len(failed) > 3 else ""
    return (f"{len(failed)}/{total} 镜未通过质检（" + "; ".join(reasons) + more + "）")


async def notify_final_node(state: CreativeSessionState) -> dict:
    from app import events
    from app.callback.java_notify import notify_java_completion

    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "notify_final", "node_name": "任务终态通知"})

    video_urls = list(state.get("video_urls") or [])
    qc_report = state.get("qc_report")
    video_error = str(state.get("video_error") or "").strip()
    # ★ 2026-09-24：`video_urls` 现在与 storyboard **等长对齐**（失败镜留空串占位），
    #   所以「有没有产物」不能再判 `not video_urls`（非空列表也可能是全空串），
    #   统一用 `any(...)` 判「至少一段真拿到产物」。
    ok_urls = [u for u in video_urls if u]
    total_shots = len(video_urls)

    if not ok_urls:
        status = TaskStatus.FAILED
        error_message = video_error or "所有镜次视频生成失败"
    else:
        status = TaskStatus.COMPLETED
        # ★ 2026-09-24 修（真跑任务 81 暴露）：原来是 `summarize_qc_report(qc) or video_error`
        #   —— **短路**。只要 QC 有任何结论（哪怕只是一条画幅不符），生成阶段的
        #   `video_error`（「第 1 段提交失败…」）就被整个丢掉：用户看到
        #   「1/1 镜未通过质检」，却完全不知道**少了一段**，成片被当成正常 completed
        #   （典型的静默降级）。现在两者都带上。
        parts = [p for p in (summarize_qc_report(qc_report), video_error) if p]
        error_message = "；".join(parts)
        # 缺段必须显式说出来（有产物、但产物比镜数少）
        if len(ok_urls) < total_shots:
            missing = [str(i + 1) for i, u in enumerate(video_urls) if not u]
            error_message = (f"{len(ok_urls)}/{total_shots} 镜有产物，缺少第 "
                             f"{'、'.join(missing)} 镜"
                             + (f"；{error_message}" if error_message else ""))
        # Java 侧 error_message 有长度上限（512），按它的口径截断
        if len(error_message) > 500:
            error_message = error_message[:497] + "..."

    # 自动修复失败时补上「修过几轮」——用户需要知道系统自己试过、不是没管
    if state.get("fix_give_up"):
        give_up_reason = str(state.get("fix_give_up_reason") or "").strip()
        if give_up_reason:
            error_message = (
                f"{error_message}；{give_up_reason}" if error_message else give_up_reason
            )

    # storyboard 必须以 JSON 字符串带回 Java —— Java 侧据此写 segments_json，
    # 而 segments_json 是「按段重生」的输入源。丢掉它等于废掉段重生功能。
    # （Java 只在 segments_json 为空时才写入，重复回调不会覆盖已有配置）
    import json as _json
    storyboard_json = _json.dumps(state.get("storyboard") or [], ensure_ascii=False)

    # 自动拼接成片（仅标准模式）。
    # 画布模式（segments 非空）在生成时已由 synthesizer 自动拼接，这里不能重复拼；
    # 标准模式此前**没有任何自动拼接环节** —— 画廊里平铺 N 个分段，用户得手点一次
    # 「拼接成片」（实测任务 38/39 至今没有成片）。纯本地 ffmpeg，不消耗生成额度。
    # 失败不阻断任务：分段仍可用，原因带回 Java 展示（与 synthesizer 同一降级哲学）。
    if status == TaskStatus.COMPLETED and not state.get("segments") and len(ok_urls) >= 2:
        from app.utils.stitch import stitch_enabled, stitch_session

        if stitch_enabled():
            await events.emit(session_id, "progress", {"progress": 95, "phase": "拼接成片"})
            try:
                stitched = await stitch_session(session_id, video_urls, allow_download=False)
                if stitched and stitched.get("final_url"):
                    video_urls = [stitched["final_url"]] + video_urls
                    await events.emit(session_id, "node_completed", {
                        "node_id": "notify_final",
                        "summary": f"自动拼接 {stitched.get('segment_count')} 段为成片",
                    })
                    logger.info("notify_final 自动拼接成片: session=%s 段数=%s",
                                session_id, stitched.get("segment_count"))
            except Exception as exc:
                reason = str(exc)[:200]
                logger.warning("notify_final 自动拼接失败: %s", reason)
                warn = f"自动拼接失败，分段视频仍可下载：{reason}"
                error_message = f"{error_message}；{warn}" if error_message else warn

    logger.info("notify_final: session=%s status=%s urls=%d error=%s",
                session_id, status, len(video_urls), error_message or "(无)")

    await events.emit(session_id, "node_completed", {
        "node_id": "notify_final",
        "summary": f"终态通知 {status}（{len(video_urls)} 个产物）",
    })

    # fire-and-forget：与既有 _notify_final 一致，不阻塞图结束
    # ★ 2026-09-19 修（#33）：带上真实总秒数，Java 的 api_quota.used_seconds
    #   才与实际生成时长挂钩（原先恒按默认 5 秒记）。
    from app.callback.java_notify import total_shot_seconds

    asyncio.create_task(
        notify_java_completion(
            session_id=session_id,
            status=str(status.value if hasattr(status, "value") else status),
            video_urls=video_urls,
            error_message=error_message or None,
            storyboard=storyboard_json,
            shot_seconds=total_shot_seconds(
                state.get("storyboard"), state.get("segments")),
        )
    )

    return {"status": status, "final_notified": True}
