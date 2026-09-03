"""LangGraph 节点：image_generator（图像生成）。

输入 prompt → 输出 image_urls。
Phase 4 P0 同步调用图像 API，结果直接写入 state。
"""
import logging
import time

from app.config import settings
from app.gateway.agnes import gateway
from app.state import CreativeSessionState, TaskStatus

logger = logging.getLogger(__name__)


async def image_generator_node(state: CreativeSessionState) -> dict:
    from app import events
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "image_generator", "node_name": "图像生成"})

    prompt = state["raw_prompt"]
    await events.emit(state["session_id"], "tool_called",
                      {"tool_name": "generate_image", "prompt": prompt})

    start = time.time()
    image_urls = await gateway.generate_image(prompt=prompt, model=settings.image_model)
    latency_ms = int((time.time() - start) * 1000)

    trace = list(state.get("trace", []))
    trace.append({
        "tool_name": "generate_image",
        "params": {"prompt": prompt, "model": settings.image_model},
        "result": {"image_urls": image_urls},
        "latency_ms": latency_ms,
        "timestamp": int(time.time()),
        "retry_count": 0,
    })

    await events.emit(state["session_id"], "node_completed",
                      {"node_id": "image_generator",
                       "summary": f"生成 {len(image_urls)} 张图片"})

    return {
        "image_urls": image_urls,
        "trace": trace,
        "status": TaskStatus.ASSET_GENERATING,
    }
