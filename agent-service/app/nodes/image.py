"""LangGraph 节点：image_generator（图像生成）。

Phase 4 P0：对每个镜次 prompt_en 逐镜生成图像，回填到对应 shot.reference_images。
生成的图像 URL 同时写入 state.image_urls 供前端画廊展示。

图生视频贯通：image_generator 产出后，storyboarder 已在 reference_images
字段填入对应图的 URL，video_generator 提交时自动走 mode="reference"（agnès Video 2.5 合法值）。
"""
import asyncio
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

    storyboard = state.get("storyboard", [])
    if not storyboard:
        logger.warning("image_generator: storyboard 为空，跳过")
        return {
            "image_urls": [],
            "trace": list(state.get("trace", [])),
            "status": state.get("status", TaskStatus.ASSET_GENERATING),
        }

    trace = list(state.get("trace", []))
    image_urls: list[str] = []

    for idx, shot in enumerate(storyboard):
        # 用户已提供参考图（storyboarder 回填的 mode="reference"）→ 跳过自动生图，尊重用户输入
        if shot.get("reference_images"):
            logger.info("shot %d 已有参考图，跳过自动生图", idx)
            continue
        prompt_en = shot.get("prompt_en", "")
        if not prompt_en:
            continue

        await events.emit(state["session_id"], "tool_called",
                          {"tool_name": "generate_image", "shot_index": idx})

        start = time.time()
        urls = await gateway.generate_image(prompt=prompt_en, model=settings.image_model)
        latency_ms = int((time.time() - start) * 1000)

        if urls:
            image_url = urls[0]
            image_urls.append(image_url)
            # 回填到对应 shot 的 reference_images
            shot["reference_images"] = [image_url]
            shot["mode"] = "reference"  # 参考图模式(agnès Video 2.5: text/keyframe/reference)

        trace.append({
            "tool_name": "generate_image",
            "params": {"prompt": prompt_en, "shot_index": idx, "model": settings.image_model},
            "result": {"image_urls": urls},
            "latency_ms": latency_ms,
            "timestamp": int(time.time()),
            "retry_count": 0,
        })

    await events.emit(state["session_id"], "node_completed",
                      {"node_id": "image_generator",
                       "summary": f"生成 {len(image_urls)} 张图片"})

    # 文生图模式：video 节点不会执行，这里直接发会话级完成回调，避免 Java 任务卡 pending
    if state.get("gen_type") == "text_image":
        from app.callback.java_notify import notify_java_completion
        asyncio.create_task(
            notify_java_completion(
                video_id="",
                session_id=state["session_id"],
                shot_index=None,
                status="completed",
                video_url="",
                image_urls=image_urls,
            )
        )
        logger.info("text_image 会话完成回调已发: session=%s, images=%d",
                    state["session_id"], len(image_urls))
        await events.emit(state["session_id"], "completed", {})

    return {
        "image_urls": image_urls,
        "storyboard": storyboard,  # 回填后的 storyboard（含 reference_images + mode）
        "trace": trace,
        "status": TaskStatus.ASSET_GENERATING,
    }
