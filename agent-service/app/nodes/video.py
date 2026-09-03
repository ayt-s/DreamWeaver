"""LangGraph 节点：video_generator（视频生成）。（Phase 2 独立轮询版）

契约：
- 工具返回 {video_id, status}，节点收集所有 Future 后统一等待
- 断点恢复：状态里已有的 video_urls 对应的镜次跳过
- 完成回调：整会话发一次，避免多镜逐条回调
"""
import asyncio
import logging
import time
from typing import Any

from app.config import settings
from app.state import CreativeSessionState, TaskStatus
from app.tools.video import generate_video_tool
from app.poller import poller
from app.gateway.agnes import gateway
from app.callback.java_notify import notify_java_completion

logger = logging.getLogger(__name__)


async def _local_poll_forever(
    video_id: str, model_name: str, session_id: str, shot_index: int,
    future: asyncio.Future,
) -> None:
    """本地轮询循环：在 future 完成前持续查询，完成后清理。"""
    waited = 0
    last_progress = -1
    while not future.done():
        await asyncio.sleep(settings.poll_interval_s)
        waited += settings.poll_interval_s
        if waited > settings.video_timeout_s:
            future.set_exception(TimeoutError(f"video {video_id} 轮询超时"))
            from app import events
            await events.emit(session_id, "error", {"error": f"video {video_id} 轮询超时"})
            return
        try:
            result = await gateway.query_video(video_id, model_name)
        except Exception as e:
            future.set_exception(e)
            from app import events
            await events.emit(session_id, "error", {"error": str(e)})
            return

        progress = result.get("progress")
        if isinstance(progress, (int, float)) and int(progress) != last_progress:
            last_progress = int(progress)
            from app import events
            await events.emit(session_id, "progress", {"progress": last_progress})

        video_url = result.get("url") or result.get("video_url")
        status = result.get("status")
        if video_url and status in ("completed", "done"):
            future.set_result({"video_url": video_url, "video_id": video_id})
            from app import events
            await events.emit(session_id, "progress", {"progress": 100})
            await notify_java_completion(
                video_id=video_id, session_id=session_id,
                shot_index=shot_index, status="completed", video_url=video_url,
            )
            return

        if result.get("error") or status in ("failed", "error"):
            err_msg = result.get("error") or f"status={status}"
            future.set_exception(RuntimeError(f"video {video_id} 生成失败: {err_msg}"))
            from app import events
            await events.emit(session_id, "error", {"error": f"video {video_id} 生成失败: {err_msg}"})
            await notify_java_completion(
                video_id=video_id, session_id=session_id,
                shot_index=shot_index, status="failed", error_message=str(err_msg),
            )
            return


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
    pending_shots: list[tuple[int, str, asyncio.Future, str]] = []

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
        )
        video_id = result["video_id"]
        future = poller.get_future(video_id)
        if future is None:
            logger.error("poller 未找到 future for video_id=%s", video_id)
            continue

        # 获取 model_name
        model_name = "agnes-video-2.5-flash"
        for tid, tinfo in poller.pending_tasks.items():
            if tid == video_id:
                model_name = tinfo["model_name"]
                break

        pending_shots.append((idx, video_id, future, model_name))

        # 启动本地轮询任务
        asyncio.create_task(
            _local_poll_forever(
                video_id=video_id, model_name=model_name,
                session_id=state["session_id"], shot_index=idx,
                future=future,
            )
        )

    # 等待所有任务完成
    if pending_shots:
        all_futures = [f for _, _, f, _ in pending_shots]
        results = await asyncio.gather(*all_futures, return_exceptions=True)

        for (idx, video_id, _, _), result in zip(pending_shots, results):
            if isinstance(result, Exception):
                trace.append({
                    "tool_name": "generate_video",
                    "params": {
                        "prompt": state["storyboard"][idx]["prompt_en"],
                        "seconds": state["storyboard"][idx]["seconds"],
                        "shot_index": idx,
                    },
                    "result": {"error": str(result)},
                    "latency_ms": 0,
                    "timestamp": int(time.time()),
                    "retry_count": 0,
                })
                await events.emit(
                    state["session_id"], "error",
                    {"error": str(result), "shot_index": idx}
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

    # 完成回调：整会话发一次，携带全量 URL 数组
    _notify_completion(state["session_id"], video_urls)

    return {
        "video_urls": video_urls,
        "video_ids": video_ids,
        "trace": trace,
        "status": TaskStatus.VIDEO_GENERATING,
    }


def _notify_completion(session_id: str, video_urls: list[str]) -> None:
    """fire-and-forget 通知 Java（不阻塞节点返回）。"""
    from app.callback.java_notify import notify_java_completion
    asyncio.create_task(
        notify_java_completion(
            video_id="",  # 整会话回调不依赖单镜 video_id，Java 按 session_id 关联
            session_id=session_id,
            shot_index=None,
            status=TaskStatus.COMPLETED,
            video_url=" ".join(video_urls),  # 兼容单值字段；主载荷走 video_urls
            video_urls=video_urls,
        )
    )
