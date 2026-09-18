"""媒体工具：输出目录、ffmpeg 定位、下载、时长探测、视频拼接。

**为什么从 nodes/synthesizer.py 抽出来（A1）**

原先这些都在 `nodes/synthesizer.py` 内部，但它的真实消费者有三个：
- `nodes/asset_fetch.py`（新增）：需要下载 —— QC 只能对本地文件跑 cv2/ffprobe，
  而 agnes 返回的是公网直链
- `nodes/image_slideshow.py`：需要下载 + 拼接
- `nodes/synthesizer.py`：需要下载兜底 + 拼接

放在任一节点里都会形成「节点 import 节点」的横向依赖（`image_slideshow` 现在就从
`synthesizer` 里 import 5 个私有符号）。放 utils 层则三个消费者都只依赖 utils，干净。

**为什么 output_root() / ffmpeg_exe() 是函数，不是模块级常量**

- 模块级常量在 import 时一次性求值 → 测试无法用 `monkeypatch.setenv` 覆盖，
  运行时改 `DREAMWEAVER_OUTPUT_DIR` 也不生效（原 `synthesizer.py` 的真实缺陷）
- `ffmpeg_exe()` 额外用 `lru_cache`：`imageio_ffmpeg.get_ffmpeg_exe()` 有磁盘检查开销，
  每次调用都重新解析不划算，但又要保留可覆盖性

**导入方式的硬约束**

调用方必须用 `from app.utils.media import download, probe_duration, ...`
（把函数绑进本模块命名空间），**不要**用 `from app.utils import media` + `media.download(...)`。
原因：多个测试用 `monkeypatch.setattr(syn_mod, "download", fake)` 这种「打模块属性」的方式
打桩，后者写法下模块里没有 `download` 属性，patch 会失效或报 AttributeError。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

import httpx
import imageio_ffmpeg

from app.utils.proc import run_command
from app.utils.retry import with_retry

logger = logging.getLogger(__name__)

# 拼接过渡时长（秒）
XFADE_TRANSITION_S = 0.5


def output_root() -> Path:
    """本地输出根目录，与 main.py 的 StaticFiles 挂载点 /v1/files 对应。

    默认 `<agent-service>/data/outputs`；可用 DREAMWEAVER_OUTPUT_DIR 覆盖（测试用）。
    """
    return Path(
        os.getenv(
            "DREAMWEAVER_OUTPUT_DIR",
            str(Path(__file__).resolve().parent.parent.parent / "data" / "outputs"),
        )
    )


def session_dir(session_id: str) -> Path:
    """单个会话的产物目录（不存在则创建）。"""
    d = output_root() / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_url(session_id: str) -> str:
    """对外可访问的本地产物 URL（经 /v1/files 静态目录，前端经 vite 代理直连）。"""
    return f"/v1/files/{session_id}/final.mp4"


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """ffmpeg 可执行文件路径（缓存，避免重复磁盘检查）。

    ⚠️ 不要改回模块级常量：那会让调用方无法在测试里覆盖，也会让
    `imageio_ffmpeg` 在 import 期就做一次磁盘扫描。
    """
    return imageio_ffmpeg.get_ffmpeg_exe()


class IncompleteDownloadError(OSError):
    """下载的字节数少于服务端声明的 Content-Length（断流残片）。

    继承 `OSError` 是**有意**的：`utils/retry.py` 的重试白名单里有 OSError，
    所以残片会自动走退避重试（多数情况下是网络抖动，重下就好）；
    重试耗尽仍失败则向上抛，让调用方记「产物缺失」而不是留一个坏文件。
    """


@with_retry("下载产物", preset="download")
async def download(url: str, dest: Path | str, timeout: float = 300.0) -> None:
    """下载远程文件到本地（agnes 返回的公网 URL）。

    带重试：wifi 抖动时按 5/15/45/90s 退避（preset="download"）。

    ⚠️ **完整性校验**（2026-09-18 新增，实测踩到）：此前只把流写完就算成功，
    而 CDN 中途断流会留下**残片**且不报错。实测 `data/outputs` 里有两段
    129~190KB 的残片（正常产物 3~8MB）：容器声明 107 帧、只能解出 11~12 帧，
    下游 QC 便拿这十几帧下结论（甚至只剩 1 个采样点）。
    现在按 Content-Length 核对实际写入字节数，不匹配即抛（并触发上面的重试）。
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            # Content-Length 描述的是**传输字节**：有 content-encoding 时解压后的长度
            # 与之不同，那种情况不校验（否则会把正常响应误判成残片）。
            expected = 0
            if not resp.headers.get("content-encoding"):
                try:
                    expected = int(resp.headers.get("content-length") or 0)
                except ValueError:
                    expected = 0
            written = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
                    written += len(chunk)
    if expected > 0 and written != expected:
        raise IncompleteDownloadError(
            f"下载不完整：{written}/{expected} 字节（残片已写入 {dest}）"
        )


async def probe_duration(path: Path | str) -> float:
    """用 ffprobe（实为 ffmpeg -i）探测视频时长（秒）；失败返回 -1.0。

    不用 asyncio.create_subprocess_exec：Windows + SelectorEventLoop（uvicorn --reload）
    下该 API 不可用，详见 app/utils/proc.py
    """
    res = await run_command([ffmpeg_exe(), "-i", str(path)], timeout=30)
    if res.timed_out:
        return -1.0
    # ffmpeg -i 会把时长写到 stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if not m:
        return -1.0
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


async def probe_dimensions(path: Path | str) -> tuple[int, int]:
    """探测视频分辨率 (width, height)；失败返回 (-1, -1)。

    用 ffmpeg -i 的 stderr 里的 `Stream #0:0 ... 1280x720` 行。
    （不额外依赖 ffprobe —— imageio-ffmpeg 只随包带 ffmpeg，不带 ffprobe）
    """
    res = await run_command([ffmpeg_exe(), "-i", str(path)], timeout=30)
    if res.timed_out:
        return (-1, -1)
    m = re.search(r"\b(\d{2,5})x(\d{2,5})\b", res.stderr)
    if not m:
        return (-1, -1)
    return (int(m.group(1)), int(m.group(2)))


async def concat_videos(inputs: list[Path], output: Path) -> bool:
    """拼接多个视频为一个文件。多段时用 xfade 交叉淡化过渡，失败降级 concat 硬切。"""
    if len(inputs) == 1:
        # 单段直接拷贝
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
        d = await probe_duration(p)
        if d <= 0:
            logger.warning("无法探测 %s 时长，降级 concat", p.name)
            return False
        durations.append(d)

    TRANSITION = XFADE_TRANSITION_S
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
        ffmpeg_exe(), "-y",
        *inputs_args,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]
    logger.info("xfade cmd: %s", " ".join(cmd[:5]) + " ... filter_complex=" + filter_complex[:200])
    res = await run_command(cmd, timeout=1200)
    if res.timed_out:
        logger.error("xfade 超时")
        return False
    if res.returncode != 0:
        logger.warning("xfade 失败 rc=%s stderr=%s", res.returncode, res.stderr[-300:])
        return False
    return output.exists() and output.stat().st_size > 0


async def _concat_videos_plain(inputs: list[Path], output: Path) -> bool:
    """ffmpeg concat demuxer 硬切拼接（xfade 失败时的兜底）。"""
    list_file = output.parent / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in inputs:
            # ffmpeg concat list 需要转义单引号
            fp = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{fp}'\n")

    cmd = [
        ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(output),
    ]
    res = await run_command(cmd, timeout=600)
    if res.timed_out:
        logger.error("ffmpeg concat 超时")
        return False
    if res.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        logger.warning("ffmpeg concat 失败 rc=%s，转码重试", res.returncode)
        # -c copy 失败（编码/参数不一致）→ 用 libx264 统一转码重试
        cmd2 = [
            ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-movflags", "+faststart",
            str(output),
        ]
        res2 = await run_command(cmd2, timeout=900)
        if res2.timed_out:
            return False
        return res2.returncode == 0 and output.exists() and output.stat().st_size > 0
    return True
