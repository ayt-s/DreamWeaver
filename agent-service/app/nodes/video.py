"""LangGraph 节点：video_generator（视频生成）。（Phase 3 独立轮询版）

契约：
- 工具返回 {video_id, status}，节点收集所有 Future 后统一等待
- 轮询完全由 VideoPoller 后台完成（future 由 poller 解决），节点不做本地轮询
- 断点恢复：状态里已有的 video_urls 对应的镜次跳过
- 完成回调：整会话发一次，避免多镜逐条回调被 Java 终态检查丢弃
"""
import asyncio
import logging
import time

from app import abort, session_store
from app.state import CreativeSessionState, TaskStatus
from app.tools.video import generate_video_tool
from app.poller import poller
from app.gateway.agnes import gateway  # noqa: F401 —— 测试 fixture 依赖本模块的 gateway 属性
from app.utils import trace as trace_util

logger = logging.getLogger(__name__)


def _effective_prompt(shot: dict) -> str:
    """提交时用的实际提示词 = 原始 prompt_en + 本轮修正后缀（fix_hint）。

    **为什么分开存**：`storyboard` 会被 `notify_final` 当作 `segments_json` 交给 Java，
    那是「按段重生」的输入基线。把修正后缀直接追加进 `prompt_en` 会污染这个基线，
    而且每轮修复都会再叠加一次、prompt 持续膨胀漂移。
    所以 `fix_looping` 只写 `fix_hint`（覆盖式），拼接发生在这里。
    """
    base = str(shot.get("prompt_en") or "")
    hint = str(shot.get("fix_hint") or "")
    return f"{base}{hint}" if hint else base


async def video_generator_node(state: CreativeSessionState) -> dict:
    from app import events
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "video_generator", "node_name": "视频生成"})
    video_urls = list(state.get("video_urls", []))
    video_ids: list[str] = list(state.get("video_ids", []))
    trace = list(state.get("trace", []))

    # 断点恢复：`done` 之前的镜次已有 URL，默认跳过不重复提交。
    done = len(video_urls)

    # 按镜次索引落位（复用段与新生段都写入对应索引），避免交错时顺序错乱。
    # done 之前的已有 URL 先按索引放入，重建时一并保留（断点恢复语义）。
    url_by_index: dict[int, str] = {i: u for i, u in enumerate(video_urls)}
    id_by_index: dict[int, str] = {i: v for i, v in enumerate(video_ids)}

    # 收集所有 Future 和对应的 shot 信息
    pending_shots: list[tuple[int, str, asyncio.Future]] = []

    for idx, shot in enumerate(state["storyboard"]):
        # 断点恢复跳过：该索引已有 URL 且**未被标记为待重生** → 不重复提交。
        #
        # ⚠️ `regenerate` 必须能越过这个跳过，否则整个自愈循环是空转：
        #   fix_looping 只清 existing_video_url，但重生轮里 `done = len(video_urls)`
        #   仍是满的 → 失败镜被这里永远跳过（实测修复轮 0 次提交、0 次重生）。
        if idx < done and not shot.get("regenerate"):
            continue
        # Java 侧已无人认领该会话（任务被重新生成/删除）→ 立刻停止后续提交，
        # 否则每一段都是一次白烧的 agnes 调用（回调会被 Java 按 session_id 丢弃）
        if abort.is_aborted(state["session_id"]):
            logger.warning("会话 %s 已中止，停止后续段提交（已处理到第 %d 段）",
                           state["session_id"], idx)
            break
        # 重生混合模式：该段已有视频 URL → 直接复用，不提交 agnes（不消耗额度）
        existing = str(shot.get("existing_video_url") or "").strip()
        if existing:
            url_by_index[idx] = existing
            id_by_index[idx] = f"reused-{idx}"
            trace = trace_util.append(
                trace, trace_util.shot("video_generator", idx), trace_util.STATUS_REUSED)
            await events.emit(state["session_id"], "progress",
                              {"phase": f"复用第 {idx + 1} 段（跳过重生）"})
            continue
        pending_id = str(shot.get("pending_video_id") or "").strip()
        if pending_id:
            # 断点恢复：该段进程重启前已提交 agnes 且仍在生成 → 复用原 video_id 继续等，
            # 绝不重新提交（重新提交 = 已花掉的额度白花两遍）
            pending_future = poller.get_future(pending_id)
            if pending_future is not None:
                pending_shots.append((idx, pending_id, pending_future))
                await events.emit(state["session_id"], "progress",
                                  {"phase": f"恢复第 {idx + 1} 段（复用已提交任务）"})
                continue
        await events.emit(state["session_id"], "tool_called",
                          {"tool_name": "generate_video", "shot_index": idx})
        result = await generate_video_tool(
            prompt=_effective_prompt(shot),
            seconds=shot["seconds"],
            mode=shot.get("mode", "text"),
            aspect_ratio=shot["aspect_ratio"],
            reference_images=shot.get("reference_images", []),
            session_id=state["session_id"],
            shot_index=idx,
            model=state.get("video_model"),
        )
        video_id = result["video_id"]
        # 会话持久化（全方案最关键的一行）：提交成功**立刻**落盘 video_id。
        # 进程此后被杀，恢复时用 query_video(video_id) 就能零成本取回结果，
        # 不需要重新提交 —— 这是 checkpointer 方案救不到的地方。
        await session_store.mark_submitted(state["session_id"], idx, video_id)
        future = poller.get_future(video_id)
        if future is None:
            logger.error("poller 未找到 future for video_id=%s", video_id)
            continue
        pending_shots.append((idx, video_id, future))

    # 等待所有任务完成（future 由 VideoPoller 后台解决，节点不轮询）
    error_msgs: list[str] = []
    if pending_shots:
        all_futures = [f for _, _, f in pending_shots]
        results = await asyncio.gather(*all_futures, return_exceptions=True)

        for (idx, video_id, _), result in zip(pending_shots, results):
            if isinstance(result, Exception):
                msg = str(result)
                error_msgs.append(msg)
                trace = trace_util.append(
                    trace, trace_util.shot("video_generator", idx), trace_util.STATUS_FAILED)
                await events.emit(
                    state["session_id"], "error",
                    {"error": msg, "shot_index": idx}
                )
            else:
                url_by_index[idx] = result["video_url"]
                id_by_index[idx] = result["video_id"]
                # 会话持久化：该段确认完成 → 落 progress.done（恢复时按索引复用）
                await session_store.mark_done(
                    state["session_id"], idx, result["video_url"], result["video_id"])
                trace = trace_util.append(
                    trace, trace_util.shot("video_generator", idx), trace_util.STATUS_OK)

    # 按镜次索引顺序重建，保证 video_urls / video_ids 与 storyboard 索引严格对齐
    video_urls = [url_by_index[i] for i in sorted(url_by_index)]
    video_ids = [id_by_index[i] for i in sorted(id_by_index)]

    # 遍历结束，把生成阶段的错误汇总进 state，供终态节点 notify_final 一次性带回 Java。
    #
    # ⚠️ 这里**刻意不再发终态回调**（Task A9）：
    #   标准模式的回调原先在这里发，位置在 QC **之前** → Java 任务立刻转终态，
    #   于是 (1) NotifyServiceImpl 的终态检查会丢弃后续回调，fix_looping 重生后的
    #   产物永远送不到 Java；(2) handleHeartbeat 对已终态任务回 tracked=false，
    #   而 main.py 把它当中止信号 → abort.mark → 自愈循环在第一轮就被自己掐死。
    #   现在回调收敛到图的真正终态（nodes/notify_final.py），保证恰好发一次。
    video_error = _format_error_msgs(error_msgs)
    if video_error:
        logger.warning("video_generator 存在失败镜次: %s", video_error)

    # 注意：本节点**不写**节点级条目 —— 图层 `_traced()` 会统一补一条带真实耗时的
    # `video_generator`。这里的逐镜条目只是**进度标记**（elapsed_ms=0）：
    # 所有镜次是批量 gather 的，等多久是整批一起等，给每条都填整批耗时会让
    # 10 镜任务显示成 10 个 90s，是假的归因。

    return {
        "video_urls": video_urls,
        "video_ids": video_ids,
        "video_error": video_error,
        "trace": trace,
        "status": TaskStatus.VIDEO_GENERATING,
    }


def _format_error_msgs(msgs: list[str]) -> str:
    """把每段的错误串成可诊断的摘要。

    格式：`seg0=<错误>; seg1=<错误>; ...`。
    单个错误超过 200 字截断，避免 Java error_message 字段过长。

    A9 起不再直接回调 Java，而是写进 `state["video_error"]`，
    由终态节点 notify_final 统一带回。
    """
    if not msgs:
        return ""
    parts = []
    for i, m in enumerate(msgs):
        s = str(m).strip()
        if len(s) > 200:
            s = s[:200] + "..."
        parts.append(f"seg{i}={s}")
    return "; ".join(parts)