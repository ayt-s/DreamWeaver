"""LangGraph 节点：video_generator（视频生成）。

契约（对应设计文档 §3.4）：
- 工具返回 {"video_url", "video_id"}，state 读写全部由本节点负责（唯一入口）
- 断点恢复：状态里已有的 video_urls 对应的镜次跳过（done = len(video_urls)）
- 完成回调：整会话发一次（带全量 URL 数组），避免多镜逐条回调导致 Java 端
  「第一镜就置终态、其余被丢弃」的数据丢失（2026-09 修复）
"""
import asyncio
import time
from typing import Any

from app.state import CreativeSessionState, TaskStatus
from app.tools.video import generate_video_tool


async def video_generator_node(state: CreativeSessionState) -> dict:
    video_urls = list(state.get("video_urls", []))
    video_ids: list[str] = list(state.get("video_ids", []))
    trace = list(state.get("trace", []))

    # 断点恢复：跳过已完成的镜次，不再重复提交
    done = len(video_urls)

    for idx, shot in enumerate(state["storyboard"][done:], start=done):
        result = await generate_video_tool(
            prompt=shot["prompt_en"],
            seconds=shot["seconds"],
            mode=shot.get("mode", "text"),
            aspect_ratio=shot["aspect_ratio"],
            reference_images=shot.get("reference_images", []),
            session_id=state["session_id"],
            shot_index=idx,
        )

        video_urls.append(result["video_url"])
        video_ids.append(result["video_id"])
        trace.append({
            "tool_name": "generate_video",
            "params": {
                "prompt": shot["prompt_en"],
                "seconds": shot["seconds"],
                "shot_index": idx,
            },
            "result": {
                "video_url": result["video_url"],
                "video_id": result["video_id"],  # 真实 Agnes 任务 ID，审计可回溯
            },
            "latency_ms": 0,  # Phase 2 由 poller 侧补充
            "timestamp": int(time.time()),
            "retry_count": 0,
        })

    # 完成回调：整会话发一次，携带全量 URL 数组（不逐镜通知）
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