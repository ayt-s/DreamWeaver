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
from app import abort
from app.state import CreativeSessionState, TaskStatus
from app.utils import trace as trace_util

logger = logging.getLogger(__name__)

# 直出图（画布节点「一键文生图」）最多带几张锚定图当参考图。
#
# 上限比视频侧的 5 张更保守：图片接口的多图上限没有官方承诺（实测 2 张 OK），
# 而常见情形只需覆盖「1~2 个角色 + 1 个场景」。前端 `FIRST_FRAME_REF_LIMIT` 同值。
FIRST_FRAME_REF_LIMIT = 4


def _backfill_reused_images(state: CreativeSessionState) -> None:
    """断点恢复：把 state 里已有的 image_urls **按索引**回填成复用字段（原地改写）。

    复用字段名与 image_generator 自身的复用分支严格一致：
    - 画布/图片重生模式（segments 非空）：回填 `segments[i].existing_image_url`
      —— 下方段重生分支读的就是它
    - 标准模式：回填 `storyboard[i].reference_images`
      —— 逐镜循环里命中 `if shot.get("reference_images")` 即跳过生成
    """
    urls = [str(u or "").strip() for u in (state.get("image_urls") or [])]
    if not any(urls):
        return
    segments = state.get("segments") or []
    if segments:
        for i, seg in enumerate(segments):
            if i < len(urls) and urls[i] and not str(seg.get("existing_image_url") or "").strip():
                seg["existing_image_url"] = urls[i]
        return
    for i, shot in enumerate(state.get("storyboard") or []):
        if i < len(urls) and urls[i] and not shot.get("reference_images"):
            shot["reference_images"] = [urls[i]]
            shot["mode"] = "reference"


def _reused_images_complete(state: CreativeSessionState) -> list[str]:
    """已有 image_urls 是否「齐」：覆盖全部镜头且每张非空。

    返回归一化后的图片列表；不齐则返回空列表（此时仍可生成缺的那些——
    因为 _backfill_reused_images 已把已有的回填成复用字段）。
    """
    urls = [str(u or "").strip() for u in (state.get("image_urls") or [])]
    expected = len(state.get("segments") or state.get("storyboard") or [])
    if not urls or expected == 0 or len(urls) < expected:
        return []
    if not all(urls[:expected]):
        return []
    return urls[:expected]


async def _finish_segment_rework(session_id: str, image_urls: list[str],
                                 storyboard: list, failed_indices: list[int]) -> None:
    """段重生/图片重生分支的收尾回调 + SSE 事件（原内联于节点内）。"""
    from app import events
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


async def _finish_text_image(session_id: str, storyboard: list,
                             image_urls: list[str]) -> None:
    """文生图/漫剧模式的会话级完成回调（原内联于节点内）。"""
    from app import events
    from app.callback.java_notify import notify_java_completion
    import json as _json

    # 带 storyboard，让 Java 保存 segments_json 供下次段重生
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


async def image_generator_node(state: CreativeSessionState) -> dict:
    from app import events

    session_id = state["session_id"]

    # 幂等守卫（断点恢复）：state 已有图片产出 → 先按索引回填复用字段，
    # 「齐了」就直接返回，绝不重复调用 agnes 生图（图片同样花钱）
    _backfill_reused_images(state)
    reused = _reused_images_complete(state)
    if reused:
        logger.info("image_generator 幂等跳过：已有 %d 张图片，按索引复用", len(reused))
        await events.emit(session_id, "node_entered",
                          {"node_id": "image_generator", "node_name": "图像生成"})
        await events.emit(session_id, "node_completed",
                          {"node_id": "image_generator",
                           "summary": f"复用已有 {len(reused)} 张图片"})
        # 跳过节点主体后，会话级完成回调必须在这里补发（否则 Java 永远等不到 completed）
        # 只有「本节点即会话终点」的两种模式才能写 COMPLETED：
        #   - 段重生（segments 非空）→ 上面已发完成回调，图之后无视频
        #   - 文生图/漫剧 → _image_route 走 text_done 直达 END
        # 标准视频模式（text_video/image_video）后面还有 video_generator，此处若写
        # COMPLETED 会让快照出现「假终态」：进程死在视频生成期间时，启动恢复会误判
        # 会话已结束而放弃恢复（P0，2026-09-14 评审）。保持中间态交给 video_generator。
        terminal = bool(state.get("segments")) or state.get("gen_type") in (
            "text_image", "comic_video")
        if state.get("segments"):
            await _finish_segment_rework(session_id, reused,
                                         state.get("storyboard") or [], [])
        elif state.get("gen_type") in ("text_image", "comic_video"):
            await _finish_text_image(session_id, state.get("storyboard") or [], reused)
        return {
            "image_urls": reused,
            "status": TaskStatus.COMPLETED if terminal
            else (state.get("status") or TaskStatus.ASSET_GENERATING),
        }

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

            # Java 侧已无人认领该会话 → 不再花钱生成
            if abort.is_aborted(session_id):
                logger.warning("会话 %s 已中止，跳过第 %d 张生成", session_id, i)
                continue

            # 需要新生成
            await events.emit(session_id, "tool_called",
                              {"tool_name": "generate_image", "segment_index": i})

            start = time.time()
            urls = await gateway.generate_image(
                prompt=cn, model=settings.image_model,
                # 画幅显式传（段配置里有）；不传服务端按 1:1 出正方形 —— 与视频画幅不匹配
                ratio=str(seg.get("aspect_ratio") or state.get("image_ratio") or ""),
            )
            latency_ms = int((time.time() - start) * 1000)

            # 逐张真耗时（这里是顺序生成，耗时能归因到具体一张；与 video 的批量
            # gather 不同，那边逐镜只能当进度标记）
            trace = trace_util.append(
                trace, trace_util.shot("image_generator", i),
                trace_util.STATUS_OK if urls else trace_util.STATUS_FAILED,
                elapsed_ms=latency_ms)

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
                # 画幅随段落库：否则下次「段重生」拿不到比例，只能退回 1:1 正方形
                "aspect_ratio": str(seg.get("aspect_ratio") or ""),
            })

        await events.emit(session_id, "node_completed",
                          {"node_id": "image_generator",
                           "summary": f"段重生：{len(image_urls)} 张（复用 {len(segments) - len([1 for s in segments if not str(s.get('existing_image_url','')).strip()])} 张）"})

        # 段重生分支也必须发完成回调，否则任务永远停在 pending（Java 不会主动轮询 agent）
        await _finish_segment_rework(session_id, image_urls, storyboard, failed_indices)
        non_empty_urls = [u for u in image_urls if u]

        return {
            "image_url": non_empty_urls[0] if non_empty_urls else "",
            "image_urls": image_urls,
            "storyboard": storyboard,
            "trace": trace,
            "status": TaskStatus.COMPLETED if non_empty_urls else TaskStatus.FAILED,
        }

    # ---- 直出图（画布节点「一键文生图」）：跳过流水线，按 prompt 直接出 N 张候选 ----
    #
    # 为什么连续请求 N 次而不是一次请求 n 张：`/images/generations` 的 `n` 不受支持
    # （2026-09-18 实测：官方参数表里根本没有 `n`），所以"多候选"只能靠同 prompt
    # 多次请求拿到。
    # ⚠️ 旧注释写「只认 model + prompt，塞未知字段会被 400 拒」——**那是错的**：
    #    `size` / `ratio` / `extra_body.image`（图生图、多图合成）都是受支持的字段
    #    （实测单图 i2i 返回 `/images/i2i/` 路径）。当初是被 `negative_prompt` 的 400
    #    外推出来的结论，别再据此少传画幅（少传 = 出 1024x1024 正方形）。
    # 与 standard 路径的区别：不会经过 requirement_parser/script_writer/storyboarder，
    # 因此不会出现「一镜的 prompt 被 LLM 拆成多镜、白生成一堆用不上的图」。
    if state.get("direct_image"):
        sid = state["session_id"]
        count = max(1, min(5, int(state.get("image_count") or 1)))
        prompt = str(state.get("raw_prompt") or "").strip()
        # ★ 锚定图当参考图（2026-09-18 A/B 实测，画布 40 真实提示词 + 真实锚定图）：
        #   带场景锚图 → 产物把参考图里的村庄/梯田/茅屋环境带出来了（纯文生只有普通山坡）；
        #   带角色锚图 → 弱收益（服装色系更贴角色卡），**不会锁脸**（i2i 对跨镜脸一致无优势）。
        #   实测不会复制主体（参考图 1 人 1 牛 → 产物仍 1 人 1 牛），画幅也不受影响。
        ref_images = [
            str(u).strip() for u in (state.get("reference_images") or []) if str(u).strip()
        ][:FIRST_FRAME_REF_LIMIT]
        logger.info("直出图模式: 候选 %d 张 | 参考图 %d 张 | prompt=%.60s",
                    count, len(ref_images), prompt)
        direct_urls: list[str] = []
        for k in range(count):
            if abort.is_aborted(sid):
                logger.warning("会话 %s 已中止，停止后续直出图（已出 %d 张）", sid, len(direct_urls))
                break
            await events.emit(sid, "tool_called",
                              {"tool_name": "generate_image", "shot_index": k})
            start = time.time()
            # 带参考图失败时**去掉参考图重试一次**：参考图是增益项，不是必需项 ——
            # 上游对多图/图生图的限制（或某张 URL 失效）不该把整个出图搞挂。
            got: list[str] = []
            attempts = [ref_images, []] if ref_images else [[]]
            for refs in attempts:
                try:
                    got = await gateway.generate_image(
                        prompt=prompt, model=settings.image_model,
                        # 画幅来自本节点（data.ratio）——不传服务端给 1:1 正方形
                        ratio=str(state.get("image_ratio") or ""),
                        reference_images=refs or None,
                    )
                except Exception as exc:  # 单张失败不影响其余候选
                    logger.warning("直出图第 %d 张%s失败: %s", k + 1,
                                   f"（带 {len(refs)} 张参考图）" if refs else "", exc)
                    got = []
                if got or not refs:
                    break
                logger.info("直出图第 %d 张去掉参考图重试一次", k + 1)
            latency_ms = int((time.time() - start) * 1000)
            if got:
                direct_urls.append(got[0])
            trace = trace_util.append(
                trace, trace_util.shot("image_generator", k),
                trace_util.STATUS_OK if got else trace_util.STATUS_FAILED,
                elapsed_ms=latency_ms)
        await events.emit(sid, "node_completed",
                          {"node_id": "image_generator",
                           "summary": f"直出图 {len(direct_urls)}/{count} 张候选"})
        # ⚠️ 回调必须在这里发：text_image 模式的图路由是 text_done → END，
        # **不经过 notify_final**，漏了这一步任务会永远停在 pending
        if direct_urls:
            # storyboard 传空：直出图没有分镜，Java 侧不会覆盖已有 segments_json
            await _finish_text_image(sid, [], direct_urls)
        else:
            from app.callback.java_notify import notify_java_completion
            asyncio.create_task(
                notify_java_completion(
                    session_id=sid,
                    status="failed",
                    error_message=f"文生图全部失败（{count} 张候选均未生成）",
                )
            )
            await events.emit(sid, "failed", {})
        return {
            "image_urls": direct_urls,
            "trace": trace,
            "status": TaskStatus.ASSET_GENERATING,
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

        # Java 侧已无人认领该会话 → 不再花钱生成（占位保持索引对齐）
        if abort.is_aborted(session_id):
            logger.warning("会话 %s 已中止，跳过第 %d 张生成", session_id, idx)
            image_urls.append("")
            continue

        await events.emit(session_id, "tool_called",
                          {"tool_name": "generate_image", "shot_index": idx})

        start = time.time()
        urls = await gateway.generate_image(
            prompt=prompt_en, model=settings.image_model,
            # 画幅取本镜的 aspect_ratio（storyboard 已归一）；不传 = 1:1 正方形
            ratio=str(shot.get("aspect_ratio") or state.get("image_ratio") or ""),
        )
        latency_ms = int((time.time() - start) * 1000)

        if urls:
            image_url = urls[0]
            image_urls.append(image_url)
            # 回填到对应 shot，供 video_generator 用
            shot["reference_images"] = [image_url]
            # ★ 首帧锁定（默认开，2026-09-18 起）：把这张图当视频的**实际第一帧**。
            #   reference 模式官方定义是「当作内容/风格/运动参考，**可能重新构图、重新计时**」
            #   —— 于是用户认可的首帧根本不是视频起点（实测「视频和我出的图不像」）。
            #   keyframe 模式官方定义才是「尝试把输入图作为实际第一帧」。
            #   关掉（lock_first_frame=False）即回到旧的 reference 行为。
            if state.get("lock_first_frame", True):
                shot["mode"] = "keyframe"
                shot["first_frame"] = image_url
            else:
                shot["mode"] = "reference"
        else:
            image_urls.append("")  # 生成失败，占位保持索引对齐

        trace = trace_util.append(
            trace, trace_util.shot("image_generator", idx),
            trace_util.STATUS_OK if urls else trace_util.STATUS_FAILED,
            elapsed_ms=latency_ms)

    await events.emit(session_id, "node_completed",
                      {"node_id": "image_generator",
                       "summary": f"生成 {len(image_urls)} 张图片"})

    # 文生图/漫剧模式：video 节点不会执行，这里直接发会话级完成回调
    if state.get("gen_type") in ("text_image", "comic_video"):
        await _finish_text_image(session_id, storyboard, image_urls)

    return {
        "image_urls": image_urls,
        "storyboard": storyboard,
        "trace": trace,
        "status": TaskStatus.ASSET_GENERATING,
    }
