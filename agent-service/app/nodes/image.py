"""图片生成节点：文生图 + 段重生复用。

文生图路径（gen_type=text_image）：
    storyboard 每个 shot 逐张生成图片，回填 reference_images + mode="reference"。

段重生模式（segments 带 existing_image_url）：
    勾选的段重新生成，未勾选的段复用 existing_image_url（跳过 agnes 调用）。
    storyboard 回传包含所有图片（新生成 + 复用），Java 保存 segments_json 供下次重生。
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

    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "image_generator", "node_name": "图像生成"})

    trace = list(state.get("trace", []))
    image_urls: list[str] = []

    # ---- 标准模式：从 storyboard 逐镜生成 ----
    storyboard = state.get("storyboard", [])
    segments = state.get("segments") or []

    if segments:
        # 段重生模式：segments 含 prompt + existing_image_url
        logger.info("段重生模式：共 %d 段", len(segments))
        storyboard = []
        # 逐段落位（复用段直接回填、重生段生成后回填），保持索引与 segments 对齐
        url_by_index: dict[int, str] = {}

        for i, seg in enumerate(segments):
            cn = str(seg.get("prompt", "")).strip()
            existing = str(seg.get("existing_image_url", "")).strip()
            if not cn:
                cn = "根据参考图生成一个相关的画面"

            if existing:
                # 复用已有图片，跳过 agnes 调用（不消耗额度）
                logger.info("段复用: %s", existing[:80])
                url_by_index[i] = existing
                await events.emit(session_id, "progress",
                                  {"phase": f"复用第 {i + 1} 张（跳过重生）"})
                continue

            # 需要新生成
            await events.emit(session_id, "tool_called",
                              {"tool_name": "generate_image", "segment_index": i})

            start = time.time()
            urls = await gateway.generate_image(prompt=cn, model=settings.image_model)
            latency_ms = int((time.time() - start) * 1000)

            trace.append({
                "tool_name": "generate_image",
                "params": {"prompt": cn, "segment_index": i, "model": settings.image_model},
                "result": {"image_urls": urls},
                "latency_ms": latency_ms,
                "timestamp": int(time.time()),
                "retry_count": 0,
            })

            if urls:
                url_by_index[i] = urls[0]

        # 按段索引收集：每段一个元素，生成失败的段用空串占位（不 continue 跳过），
        # 保持 image_urls 与 segments 索引严格对齐——Java 侧按索引落库/取图。
        image_urls: list[str] = []
        failed_indices: list[int] = []
        for i, seg in enumerate(segments):
            u = url_by_index.get(i, "")
            if not u:
                u = str(seg.get("existing_image_url", "")).strip()
            if not u:
                failed_indices.append(i)
                image_urls.append("")  # 失败段空串占位，保持索引对齐
                continue
            image_urls.append(u)

        # 构建 storyboard（包含所有图片：新生成 + 复用），供 Java 保存 segments_json
        # image_url 直接取 image_urls[i]，保证与落库列表逐段一致（失败段同样为空串）
        storyboard = []
        for i, seg in enumerate(segments):
            storyboard.append({
                "id": i,
                "prompt": str(seg.get("prompt", "")),
                "prompt_en": str(seg.get("prompt_en", "")),
                "image_url": image_urls[i],
                "reference_images": list(seg.get("reference_images") or []),
            })

        await events.emit(session_id, "node_completed",
                          {"node_id": "image_generator",
                           "summary": f"段重生：{len(image_urls)} 张（复用 {len(segments) - len([1 for s in segments if not str(s.get('existing_image_url','')).strip()])} 张）"})

        # 段重生分支也必须发完成回调，否则任务永远停在 pending（Java 不会主动轮询 agent）
        from app.callback.java_notify import notify_java_completion
        import json as _json
        # 「全部失败」判定：非空 URL 数量为 0（列表长度恒等于 segments 数，含空串占位）
        non_empty_urls = [u for u in image_urls if u]
        if non_empty_urls:
            asyncio.create_task(
                notify_java_completion(
                    session_id=session_id,
                    status="completed",
                    image_urls=image_urls,  # 完整列表（含空串），索引与 segments 对齐
                    storyboard=_json.dumps(storyboard, ensure_ascii=False),
                )
            )
            await events.emit(session_id, "completed", {})
        else:
            asyncio.create_task(
                notify_java_completion(
                    session_id=session_id,
                    status="failed",
                    error_message=f"段重生全部失败（段索引 {failed_indices}）",
                )
            )
            await events.emit(session_id, "failed", {})

        return {
            "image_url": non_empty_urls[0] if non_empty_urls else "",
            "image_urls": image_urls,
            "storyboard": storyboard,
            "trace": trace,
            "status": TaskStatus.COMPLETED if non_empty_urls else TaskStatus.FAILED,
        }

    # ---- 文生图模式：storyboard 逐镜生成 ----
    if not storyboard:
        logger.warning("image_generator: storyboard 为空，跳过")
        return {
            "image_urls": [],
            "trace": trace,
            "status": state.get("status", TaskStatus.ASSET_GENERATING),
        }

    for idx, shot in enumerate(storyboard):
        # 用户已提供参考图 → 跳过自动生图，尊重用户输入
        if shot.get("reference_images"):
            logger.info("shot %d 已有参考图，跳过自动生图", idx)
            image_urls.append("")  # 占位，保持索引对齐
            continue
        prompt_en = shot.get("prompt_en", "")
        if not prompt_en:
            image_urls.append("")  # 占位，保持索引对齐
            continue

        await events.emit(session_id, "tool_called",
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
        else:
            image_urls.append("")  # 生成失败，占位保持索引对齐

        trace.append({
            "tool_name": "generate_image",
            "params": {"prompt": prompt_en, "shot_index": idx, "model": settings.image_model},
            "result": {"image_urls": urls},
            "latency_ms": latency_ms,
            "timestamp": int(time.time()),
            "retry_count": 0,
        })

    await events.emit(session_id, "node_completed",
                      {"node_id": "image_generator",
                       "summary": f"生成 {len(image_urls)} 张图片"})

    # 文生图/漫剧模式：video 节点不会执行，这里直接发会话级完成回调
    if state.get("gen_type") in ("text_image", "comic_video"):
        from app.callback.java_notify import notify_java_completion
        # 带 storyboard，让 Java 保存 segments_json 供下次段重生
        import json as _json
        sb_json = _json.dumps(storyboard, ensure_ascii=False)
        asyncio.create_task(
            notify_java_completion(
                video_id="",
                session_id=session_id,
                shot_index=None,
                status="completed",
                video_url="",
                image_urls=[u for u in image_urls if u],
                storyboard=sb_json,
            )
        )
        logger.info("text_image/comic_video 会话完成回调已发: session=%s, images=%d",
                    session_id, len(image_urls))
        await events.emit(session_id, "completed", {})

    return {
        "image_urls": image_urls,
        "storyboard": storyboard,
        "trace": trace,
        "status": TaskStatus.ASSET_GENERATING,
    }
