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

from app.state import CreativeSessionState, TaskStatus
from app.tools.video import generate_video_tool
from app.poller import poller
from app.gateway.agnes import gateway  # noqa: F401 —— 测试 fixture 依赖本模块的 gateway 属性

logger = logging.getLogger(__name__)


async def video_generator_node(state: CreativeSessionState) -> dict:
    from app import events
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "video_generator", "node_name": "视频生成"})
    video_urls = list(state.get("video_urls", []))
    video_ids: list[str] = list(state.get("video_ids", []))
    trace = list(state.get("trace", []))

    # 断点恢复：跳过已完成的镜次，不再重复提交
    done = len(video_urls)

    # 收集所有 Future 和对应的 shot 信息
    pending_shots: list[tuple[int, str, asyncio.Future]] = []

    for idx, shot in enumerate(state["storyboard"][done:], start=done):
        await events.emit(state["session_id"], "tool_called",
                          {"tool_name": "generate_video", "shot_index": idx})
        result = await generate_video_tool(
            prompt=shot["prompt_en"],
            seconds=shot["seconds"],
            mode=shot.get("mode", "text"),
            aspect_ratio=shot["aspect_ratio"],
            reference_images=shot.get("reference_images", []),
            session_id=state["session_id"],
            shot_index=idx,
            model=state.get("video_model"),
        )
        video_id = result["video_id"]
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
                trace.append({
                    "tool_name": "generate_video",
                    "params": {
                        "prompt": state["storyboard"][idx]["prompt_en"],
                        "seconds": state["storyboard"][idx]["seconds"],
                        "shot_index": idx,
                    },
                    "result": {"error": msg},
                    "latency_ms": 0,
                    "timestamp": int(time.time()),
                    "retry_count": 0,
                })
                await events.emit(
                    state["session_id"], "error",
                    {"error": msg, "shot_index": idx}
                )
            else:
                video_urls.append(result["video_url"])
                video_ids.append(result["video_id"])
                trace.append({
                    "tool_name": "generate_video",
                    "params": {
                        "prompt": state["storyboard"][idx]["prompt_en"],
                        "seconds": state["storyboard"][idx]["seconds"],
                        "shot_index": idx,
                    },
                    "result": {
                        "video_url": result["video_url"],
                        "video_id": result["video_id"],
                    },
                    "latency_ms": 0,
                    "timestamp": int(time.time()),
                    "retry_count": 0,
                })

    # 完成回调：标准模式整会话发一次，携带全量 URL 数组；全镜失败则发失败态。
        # 画布模式（segments 非空）不回这里发完成通知——synthesizer 拼接出长视频后统一回调，
        # 避免 Java 任务先被 completed 落定、后续拼接 URL 无法再更新。
        canvas_mode = bool(state.get("segments"))
        if not canvas_mode:
            if video_urls:
                _notify_unified(
                    state["session_id"], TaskStatus.COMPLETED, video_urls=video_urls
                )
            else:
                _notify_unified(
                    state["session_id"], TaskStatus.FAILED,
                    error_message="; ".join(error_msgs) or "所有镜次视频生成失败",
                )
        else:
            # 画布模式全镜失败 → 也补发失败态（否则 Java 任务永远 pending）
            if not video_urls:
                _notify_unified(
                    state["session_id"], TaskStatus.FAILED,
                    error_message="; ".join(error_msgs) or "所有片段视频生成失败",
                )
            else:
                logger.info(
                    "画布模式 video_generator 完成（%d 段），完成回调推迟到 synthesizer",
                    len(video_urls),
                )

    return {
        "video_urls": video_urls,
        "video_ids": video_ids,
        "trace": trace,
        "status": TaskStatus.VIDEO_GENERATING,
    }


def _notify_unified(session_id: str, status: str,
                    video_urls: list[str] | None = None,
                    error_message: str | None = None) -> None:
    """fire-and-forget 通知 Java（不阻塞节点返回）。"""
    from app.callback.java_notify import notify_java_completion
    asyncio.create_task(
        notify_java_completion(
            video_id="",  # 整会话回调不依赖单镜 video_id，Java 按 session_id 关联
            session_id=session_id,
            shot_index=None,
            status=status,
            video_url=" ".join(video_urls or []),  # 兼容单值字段；主载荷走 video_urls
            video_urls=video_urls or [],
            error_message=error_message,
        )
    )