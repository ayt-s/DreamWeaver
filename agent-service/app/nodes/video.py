"""LangGraph 节点：video_generator（视频生成）。

契约（对应设计文档 §3.4）：
- 工具只返回单个 video_url，state 读写全部由本节点负责（唯一入口）
- 断点恢复：状态里已有的 video_urls 对应的镜次跳过（done = len(video_urls)）
"""
import time

from app.state import CreativeSessionState, TaskStatus
from app.tools.video import generate_video_tool


async def video_generator_node(state: CreativeSessionState) -> dict:
    video_urls = list(state.get("video_urls", []))
    trace = list(state.get("trace", []))

    # 断点恢复：跳过已完成的镜次，不再重复提交
    done = len(video_urls)

    for idx, shot in enumerate(state["storyboard"][done:], start=done):
        video_url = await generate_video_tool(
            prompt=shot["prompt_en"],
            seconds=shot["seconds"],
            mode=shot.get("mode", "text"),
            aspect_ratio=shot["aspect_ratio"],
            reference_images=shot.get("reference_images", []),
            session_id=state["session_id"],
            shot_index=idx,
        )

        video_urls.append(video_url)
        trace.append({
            "tool_name": "generate_video",
            "params": {
                "prompt": shot["prompt_en"],
                "seconds": shot["seconds"],
                "shot_index": idx,
            },
            "result": {"video_url": video_url},
            "latency_ms": 0,  # Phase 2 由 poller 侧补充
            "timestamp": int(time.time()),
            "retry_count": 0,
        })

    # Phase 2 回调通知：视频生成完成后通知 Java 更新状态
    from app.callback.java_notify import notify_java_completion
    for idx, url in enumerate(video_urls[done:], start=done):
        asyncio.create_task(
            notify_java_completion(
                video_id=shot.get("video_id", f"shot_{idx}"),
                session_id=state["session_id"],
                shot_index=idx,
                status=TaskStatus.COMPLETED,
                video_url=url,
            )
        )

    return {
        "video_urls": video_urls,
        "trace": trace,
        "status": TaskStatus.VIDEO_GENERATING,
    }