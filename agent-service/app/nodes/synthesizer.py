"""LangGraph 节点：synthesizer（多镜拼接长视频）。

无限画布模式（segments 存在）下，video_generator 产出的是每段几秒的小视频，
本节点把它们下载到本地，用 ffmpeg concat 拼成一条长视频，输出到
<本地输出目录>/<session>/final.mp4，并经 FastAPI 静态目录（/v1/files）对外可访问。

单段视频也走这里：拷贝为 final.mp4（不拼接），保证产物字段统一。

失败处理：任一环节失败不阻断任务——降级为直接透传原 video_urls。
"""
import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
import imageio_ffmpeg

from app.config import settings
from app.state import CreativeSessionState, TaskStatus
from app.utils.retry import with_retry

logger = logging.getLogger(__name__)

# 本地输出根目录（与 main.py 的 StaticFiles 挂载点 /v1/files 对应）
OUTPUT_ROOT = Path(
    os.getenv("DREAMWEAVER_OUTPUT_DIR", str(Path(__file__).resolve().parent.parent.parent / "data" / "outputs"))
)
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()


@with_retry("下载产物", preset="download")
async def _download(url: str, dest: Path, timeout: float = 300.0) -> None:
    """下载远程视频到本地（Agnes 返回的公网 URL）。带重试：wifi 抖动时按 5/15/45/90s 退避。"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)


async def _probe_duration(path: Path) -> float:
    """用 ffprobe 探测视频时长（秒）。失败返回 -1。"""
    cmd = [
        FFMPEG_EXE, "-i", str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        return -1.0
    # ffmpeg -i 会把时长写到 stderr
    text = stderr.decode("utf-8", errors="replace")
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return -1.0
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


async def _concat_videos(inputs: list[Path], output: Path) -> bool:
    """ffmpeg 拼接视频。多段时用 xfade 做交叉淡化过渡，失败降级到 concat 硬切。"""
    if len(inputs) == 1:
        # 单段直接拷贝
        import shutil
        shutil.copyfile(inputs[0], output)
        return output.exists() and output.stat().st_size > 0

    # 多段：先尝试 xfade 过渡，失败降级到 concat
    xfade_ok = await _concat_with_xfade(inputs, output)
    if xfade_ok:
        return True
    logger.warning("xfade 失败，降级到 concat 硬切")
    return await _concat_videos_plain(inputs, output)


async def _concat_with_xfade(inputs: list[Path], output: Path) -> bool:
    """ffmpeg filter_complex xfade 交叉淡化。过渡 0.5 秒。"""
    # 探测每段时长
    durations = []
    for p in inputs:
        d = await _probe_duration(p)
        if d <= 0:
            logger.warning("无法探测 %s 时长，降级 concat", p.name)
            return False
        durations.append(d)

    # 每个 transition 0.5 秒；offset[i] = 前面所有段的累计时长 - 过渡时长 * i
    TRANSITION = 0.5
    n = len(inputs)
    inputs_args: list[str] = []
    for p in inputs:
        inputs_args.extend(["-i", str(p)])

    # 构建 filter_complex：xfade 链式串联
    # [0:v][1:v]xfade=transition=fade:duration=0.5:offset=D0-0.5[v01];
    # [v01][2:v]xfade=transition=fade:duration=0.5:offset=D0+D1-0.5*2[v012]; ...
    filter_parts: list[str] = []
    for i in range(n - 1):
        if i == 0:
            src_left = "[0:v]"
        else:
            src_left = f"[v0{i}]"
        src_right = f"[{i+1}:v]"
        if i == n - 2:
            out_label = "[vout]"
        else:
            out_label = f"[v0{i+1}]"
        # offset = 前 i+1 段累计时长 - 已用过渡时长 * i - 本次过渡时长
        cumulative = sum(durations[: i + 1])
        offset = cumulative - TRANSITION * (i + 1)
        if offset < 0:
            # 段太短放不下过渡，跳过 xfade
            return False
        filter_parts.append(
            f"{src_left}{src_right}xfade=transition=fade:duration={TRANSITION}:offset={offset:.3f}{out_label}"
        )

    filter_complex = ";".join(filter_parts)
    cmd = [
        FFMPEG_EXE, "-y",
        *inputs_args,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]
    logger.info("xfade cmd: %s", " ".join(cmd[:5]) + " ... filter_complex=" + filter_complex[:200])
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=1200)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("xfade 超时")
        return False
    if proc.returncode != 0:
        logger.warning("xfade 失败 rc=%s stderr=%s", proc.returncode, stderr.decode("utf-8", errors="replace")[-300:])
        return False
    return output.exists() and output.stat().st_size > 0


async def _concat_videos_plain(inputs: list[Path], output: Path) -> bool:
    """ffmpeg concat demuxer 硬切拼接（原逻辑，作为 xfade 失败兜底）。"""
    list_file = output.parent / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in inputs:
            # ffmpeg concat list 需要转义单引号
            fp = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{fp}'\n")

    cmd = [
        FFMPEG_EXE, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        logger.error("ffmpeg concat 超时")
        return False
    if rc != 0 or not output.exists() or output.stat().st_size == 0:
        logger.warning("ffmpeg concat 失败 rc=%s，转码重试", rc)
        # -c copy 失败（编码/参数不一致）→ 用 libx264 统一转码重试
        cmd2 = [
            FFMPEG_EXE, "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-movflags", "+faststart",
            str(output),
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd2, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            rc2 = await asyncio.wait_for(proc2.wait(), timeout=900)
        except asyncio.TimeoutError:
            proc2.kill()
            return False
        return rc2 == 0 and output.exists() and output.stat().st_size > 0
    return True


def _local_url(session_id: str) -> str:
    """生成对外可访问的本地产物 URL（经 /v1/files 静态目录，前端经 vite 代理直连）。"""
    return f"/v1/files/{session_id}/final.mp4"


async def _notify_final(session_id: str, status: str, video_urls: list[str],
                        error_message: str | None = None) -> None:
    """画布模式最终完成回调：携带拼接后的长视频 URL（放首位）+ 各分段 URL。"""
    from app.callback.java_notify import notify_java_completion
    asyncio.create_task(
        notify_java_completion(
            video_id="",
            session_id=session_id,
            shot_index=None,
            status=status,
            video_urls=video_urls,
            error_message=error_message,
        )
    )


async def synthesizer_node(state: CreativeSessionState) -> dict:
    from app import events
    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "synthesizer", "node_name": "多镜拼接"})

    video_urls = list(state.get("video_urls", []))
    if not video_urls:
        logger.warning("synthesizer: 无视频可拼接")
        await events.emit(session_id, "node_completed",
                          {"node_id": "synthesizer", "summary": "无视频，跳过拼接"})
        return {"status": TaskStatus.SYNTHESIZING, "final_video_url": ""}

    shot_dir = OUTPUT_ROOT / session_id
    shot_dir.mkdir(parents=True, exist_ok=True)
    local_files: list[Path] = []
    try:
        for i, url in enumerate(video_urls):
            dest = shot_dir / f"seg_{i:03d}.mp4"
            await _download(url, dest)
            local_files.append(dest)
            await events.emit(session_id, "progress",
                              {"progress": int((i + 1) / len(video_urls) * 50), "phase": "下载分段"})
            logger.info("synthesizer: 下载分段 %d/%d → %s", i + 1, len(video_urls), dest.name)

        final_mp4 = shot_dir / "final.mp4"
        ok = await _concat_videos(local_files, final_mp4)
        if not ok:
            raise RuntimeError("ffmpeg 拼接失败")

        final_url = _local_url(session_id)
        await events.emit(session_id, "progress",
                          {"progress": 100, "phase": "拼接完成"})
        logger.info("synthesizer: 长视频生成 %s (%d bytes)", final_url, final_mp4.stat().st_size)
        await events.emit(session_id, "node_completed",
                          {"node_id": "synthesizer", "summary": f"拼接 {len(video_urls)} 段为长视频"})

        # 最终回调：长视频在前，分段在后（Java 任务 result_json 全量落库）
        await _notify_final(session_id, "completed", [final_url] + video_urls)
        await events.emit(session_id, "completed", {})
        return {
            "final_video_url": final_url,
            "status": TaskStatus.SYNTHESIZING,
        }
    except Exception as exc:
        # 拼接失败不阻断任务：原 video_urls 已由 synthesizer 兜底回调，前端仍可见各分段
        logger.error("synthesizer 失败: %s", exc, exc_info=True)
        await events.emit(session_id, "error",
                          {"error": f"多镜拼接失败: {exc}"})
        await events.emit(session_id, "node_completed",
                          {"node_id": "synthesizer", "summary": f"拼接失败，透传 {len(video_urls)} 段"})
        await _notify_final(session_id, "completed", video_urls,
                            error_message=f"多镜拼接失败: {exc}")
        await events.emit(session_id, "completed", {})
        return {
            "final_video_url": "",
            "status": TaskStatus.SYNTHESIZING,
        }