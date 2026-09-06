"""LangGraph 节点：image_slideshow（图片合成视频）。

从一批图片 URL 直接拼成一条视频：每张图片按 slide_seconds 停留，
用 xfade 做交叉淡化过渡。比「图片逐张生成视频再拼接」省 agnes 额度、速度快一个量级，
适合「文生图/漫剧产出 → 挑几张合成成片」的场景。

复用 synthesizer 的下载与拼接工具；失败降级：拼接失败则透传原图，不阻断任务。
"""
import asyncio
import logging
import time
from pathlib import Path

from app.callback.java_notify import notify_java_completion
from app.config import settings
from app.nodes.synthesizer import (
    OUTPUT_ROOT,
    FFMPEG_EXE,
    _concat_videos,
    _download,
    _probe_duration,
)
from app.state import CreativeSessionState, TaskStatus

logger = logging.getLogger(__name__)

# 单图停留默认秒数；范围 1~10，前端可传
DEFAULT_SLIDE_SECONDS = 3.0
MIN_SLIDE_SECONDS = 1.0
MAX_SLIDE_SECONDS = 10.0
# 输出画幅（与视频任务默认一致）
OUT_W, OUT_H = 1280, 720


async def _image_to_clip(img_path: Path, dest: Path, seconds: float) -> bool:
    """把单张图片做成指定时长的视频片段。

    图片缩放适配 OUT_W×OUT_H 并黑边填充，避免不同尺寸图拼接时报错；
    加缓慢缩放（Ken Burns）让画面有动态感，纯静帧拼接太死板。
    """
    vf = (
        f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2,"
        f"zoompan=z='min(zoom+0.0008,1.08)':d={int(seconds * 30)}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={OUT_W}x{OUT_H}:fps=30"
    )
    cmd = [
        FFMPEG_EXE, "-y",
        "-loop", "1", "-i", str(img_path),
        "-t", str(seconds),
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", "30",
        str(dest),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        return False
    if not dest.exists() or dest.stat().st_size <= 0:
        logger.warning("图片转片段失败 %s: %s", img_path.name, stderr.decode("utf-8", "replace")[-300:])
        return False
    return True


async def image_slideshow_node(state: CreativeSessionState) -> dict:
    from app import events

    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "image_slideshow", "node_name": "图片合成视频"})

    images = list(state.get("slideshow_images") or [])
    raw_seconds = float(state.get("slide_seconds") or DEFAULT_SLIDE_SECONDS)
    seconds = max(MIN_SLIDE_SECONDS, min(raw_seconds, MAX_SLIDE_SECONDS))
    trace = list(state.get("trace", []))

    if len(images) < 2:
        msg = f"合成视频至少需要 2 张图片（当前 {len(images)} 张）"
        logger.warning("image_slideshow: %s", msg)
        await events.emit(session_id, "error", {"error": msg})
        asyncio.create_task(notify_java_completion(
            session_id=session_id, status="failed", error_message=msg))
        await events.emit(session_id, "failed", {})
        return {"status": TaskStatus.FAILED, "trace": trace}

    out_dir = OUTPUT_ROOT / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 下载图片 + 逐张转视频片段
    clips: list[Path] = []
    await events.emit(session_id, "progress",
                      {"phase": f"下载 {len(images)} 张图片（每张 {seconds:g}s）"})
    for i, url in enumerate(images):
        if not str(url).startswith(("http://", "https://")):
            continue
        dest = out_dir / f"clip_{i:03d}.mp4"
        img_path = out_dir / f"img_{i:03d}"
        try:
            await _download(url, img_path, timeout=180)
        except Exception as exc:
            logger.warning("下载第 %d 张图片失败: %s", i, exc)
            continue
        if not img_path.exists() or img_path.stat().st_size <= 0:
            continue
        t0 = time.time()
        if not await _image_to_clip(img_path, dest, seconds):
            continue
        clips.append(dest)
        trace.append({
            "tool_name": "image_to_clip",
            "params": {"image_url": url, "seconds": seconds},
            "result": {"clip": dest.name},
            "latency_ms": int((time.time() - t0) * 1000),
            "timestamp": int(time.time()),
            "retry_count": 0,
        })

    if len(clips) < 2:
        msg = f"可用图片不足 2 张（仅 {len(clips)} 张），无法合成视频"
        logger.warning("image_slideshow: %s", msg)
        await events.emit(session_id, "error", {"error": msg})
        asyncio.create_task(notify_java_completion(
            session_id=session_id, status="failed", error_message=msg))
        await events.emit(session_id, "failed", {})
        return {"status": TaskStatus.FAILED, "trace": trace}

    # 2. 拼接（多段走 xfade 过渡，失败降级硬切）
    final_path = out_dir / "final.mp4"
    await events.emit(session_id, "progress",
                      {"phase": f"拼接 {len(clips)} 段为长视频"})
    ok = await _concat_videos(clips, final_path)
    if not ok:
        msg = "视频拼接失败"
        logger.error("image_slideshow 拼接失败")
        await events.emit(session_id, "error", {"error": msg})
        asyncio.create_task(notify_java_completion(
            session_id=session_id, status="failed", error_message=msg))
        await events.emit(session_id, "failed", {})
        return {"status": TaskStatus.FAILED, "trace": trace}

    final_url = f"/v1/files/{session_id}/final.mp4"
    duration = await _probe_duration(final_path)
    logger.info("image_slideshow: %d 张图 → %s (%.1fs, %d bytes)",
                len(clips), final_url, duration, final_path.stat().st_size)

    await events.emit(session_id, "node_completed",
                      {"node_id": "image_slideshow",
                       "summary": f"{len(clips)} 张图合成 {duration:.0f}s 视频"})

    # 3. 完成回调：video_urls 单元素（成片），Java 存入 result_json
    asyncio.create_task(notify_java_completion(
        session_id=session_id,
        status="completed",
        video_url=final_url,
        video_urls=[final_url],
    ))
    await events.emit(session_id, "completed", {})

    return {
        "video_url": final_url,
        "video_urls": [final_url],
        "final_video_url": final_url,
        "image_urls": images,
        "trace": trace,
        "status": TaskStatus.COMPLETED,
    }
