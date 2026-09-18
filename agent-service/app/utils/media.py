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

import json
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


async def probe_streams(path: Path | str) -> tuple[float, bool, float]:
    """一次 `ffmpeg -i` 拿到 (时长/秒, 是否有音轨, fps)。探测不到给安全默认值。

    拼接前这三样都要：**时长**算 xfade 的 offset；**音轨**决定要不要接音频链；
    **fps** 用来统一各路 timebase —— `xfade` 对 timebase 是硬要求，实测
    24fps 的真实段 + 30fps 的幻灯片段会直接
    `First input link main timebase (1/12288) do not match ... (1/15360)` 报 -22，
    整条拼接失败降级成硬切（连过渡都没了）。
    用一次探测取代「时长一次 + 音轨一次」，避免每段起两个进程。
    """
    res = await run_command([ffmpeg_exe(), "-i", str(path)], timeout=30)
    if res.timed_out:
        return (-1.0, False, 0.0)
    err = res.stderr or ""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    secs = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else -1.0
    fps_m = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
    return (secs, "Audio:" in err, float(fps_m.group(1)) if fps_m else 0.0)


async def probe_has_audio(path: Path | str) -> bool:
    """探测视频是否带音轨（用 ffmpeg -i 的 stderr，同 probe_dimensions 的路子）。

    为什么需要：`acrossfade` 要求**每一路都有音频流**，只要有一段没有（例如
    「图片幻灯片」段是 ffmpeg 从静图生成的、本来就没有音轨），整条音频链会失败，
    连带把视频过渡也拖垮。所以先逐段探、给缺的垫静音。
    """
    res = await run_command([ffmpeg_exe(), "-i", str(path)], timeout=30)
    if res.timed_out:
        return False
    return "Audio:" in (res.stderr or "")


# ---- 音量归一化参数（2026-09-18 实测驱动）------------------------------------
# 实测前提：10 个真实段的整合响度 I 中位数 -28.3 LUFS、范围 -42.6 ~ -14.7，
# 即**段间差 27.9 LU**、整体还偏轻 12 LU。而段间差 >3 LU 就能明显听出忽大忽小，
# 所以「音轨接回来了」不等于「听着正常」——必须逐段把响度拉齐。
#
# 用**静态增益**（`volume=dB`）而不是逐段 `loudnorm`：段只有 4.5s 左右，
# 而 loudnorm 的 gating 窗口就是 3s，动态模式在这么短的片段上容易抽气/过冲；
# 静态增益是确定性的，也便于验证（按段时间切片重量，段间差应落到 1~2 LU）。
#
# 增益同时受**真实峰值上限**约束（`min(响度目标差, 峰值余量)`），否则把 -38 LUFS
# 的段落拉到 -16 LUFS（+22 dB）会把削波风险一起放大。
AUDIO_TARGET_LUFS = -16.0        # 网络交付常用目标
AUDIO_TP_CEILING_DBTP = -1.5     # 真实峰值上限（留 1.5 dB 余量）
AUDIO_GAIN_LIMIT_DB = 24.0       # 单段增益上限，避免把噪声地板抬成主角
AUDIO_SILENCE_LUFS = -70.0       # 低于此视为「有音轨但实质静音」，不做增益


async def probe_loudness(path: Path | str) -> tuple[float, float] | None:
    """探测整合响度与真实峰值 → (I/LUFS, TP/dBTP)；探测失败返回 None。

    用 `loudnorm=print_format=json`（它会把测量结果以 JSON 打到 stderr，
    需完整解码一遍，故每段多一次解码开销；换来的是确定性增益，值得）。
    """
    res = await run_command([
        ffmpeg_exe(), "-hide_banner", "-i", str(path),
        "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ], timeout=300)
    if res.timed_out:
        return None
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", res.stderr or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        i = float(data["input_i"])
        tp = float(data["input_tp"])
    except (ValueError, KeyError, TypeError):
        return None
    if i < -99:  # loudnorm 对纯静音会给 -inf
        return None
    return (i, tp)


def _loudness_gain_db(loudness: tuple[float, float] | None) -> float:
    """按测量值算静态增益（dB）：响度拉到目标，同时不越过真实峰值上限。"""
    if loudness is None:
        return 0.0
    i, tp = loudness
    if i <= AUDIO_SILENCE_LUFS:
        return 0.0
    gain = min(AUDIO_TARGET_LUFS - i, AUDIO_TP_CEILING_DBTP - tp)
    return max(-AUDIO_GAIN_LIMIT_DB, min(AUDIO_GAIN_LIMIT_DB, gain))


async def _concat_with_xfade(inputs: list[Path], output: Path) -> bool:
    """ffmpeg filter_complex xfade 交叉淡化。过渡 0.5 秒。

    ⚠️ 两条硬要求（都是 2026-09-18 实测撞出来的，改这块前先看）：

    1. **音频必须一起接**。此前 `filter_complex` 只建了视频链、`-map` 只给 `[vout]`，
       于是**多段成片整条没有声音** —— 而平台给的段是带 aac 音轨的，纯属本地拼接丢的。
       实测：抽样 14/14 段都有音轨，成片 9/10 没有；唯一带音轨的那条是**单段**
       （走 `shutil.copyfile`，没经过这条路径）。时长也对得上：24.4s = 6×4.5 − 5×0.5。
       音频用 `acrossfade`，与视频 `xfade` 一一对应（同样 0.5s、同样次数）。
    2. **每路必须先统一 fps 与 timebase**。xfade 要求两个输入 timebase 完全一致，
       24fps 真实段配 30fps 幻灯片段会报 `timebase (1/12288) do not match (1/15360)`
       并整条失败（→ 降级硬切，过渡没了）。所以先 `fps=<首路>` + `settb=AVTB`。
    """
    # 探测每段时长 / 音轨 / fps
    infos = [await probe_streams(p) for p in inputs]
    durations = [i[0] for i in infos]
    for p, d in zip(inputs, durations):
        if d <= 0:
            logger.warning("无法探测 %s 时长，降级 concat", p.name)
            return False
    has_audio = [i[1] for i in infos]
    # 以第一路 fps 为基准（全真实段时行为不变；混了幻灯片段才起作用）
    fps = infos[0][2] or 24.0

    TRANSITION = XFADE_TRANSITION_S
    n = len(inputs)
    inputs_args: list[str] = []
    for p in inputs:
        inputs_args.extend(["-i", str(p)])

    # 构建 filter_complex：先逐路归一化（强制 CFR + 统一 timebase），再 xfade 链式串联
    # [vi0][vi1]xfade=transition=fade:duration=0.5:offset=D0-0.5[v01];
    # [v01][vi2]xfade=...:offset=D0+D1-0.5*2[vout]
    #
    # ⚠️ 归一化**只能用 `fps=`，不要加 `setpts=PTS-STARTPTS`**：实测 setpts 会把链路
    # 的帧率元数据清成 1/0，xfade 立刻报
    # `The inputs needs to be a constant frame rate; current rate of 1/0 is invalid`
    # （对照组：只 `fps=24`、只 `null`、输入前 `-r 24` 三种都正常）。
    # `fps=` 一个滤镜就同时满足「CFR」和「timebase 一致」两条，settb/setpts 都是多余且有害。
    filter_parts: list[str] = []
    for i in range(n):
        filter_parts.append(f"[{i}:v]fps={fps}[vi{i}]")
    for i in range(n - 1):
        src_left = "[vi0]" if i == 0 else f"[v0{i}]"
        src_right = f"[vi{i+1}]"
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

    # 音频链：逐段「量响度 → 静态增益」拉到同一目标，再 acrossfade。
    # 没音轨的段垫一段等长静音（接在真实输入之后，索引 = len(inputs)+k），
    # 否则 acrossfade 会因为缺流而整体失败 —— 那样就又回到「没声音」。
    with_audio = any(has_audio)
    if with_audio:
        silent_added = 0
        audio_labels: list[str] = []
        gains: list[float] = []
        for i, has in enumerate(has_audio):
            if not has:
                idx = n + silent_added
                inputs_args.extend([
                    "-f", "lavfi", "-t", f"{durations[i]:.3f}",
                    "-i", "anullsrc=r=44100:cl=stereo",
                ])
                silent_added += 1
                src = f"[{idx}:a]"
                gain = 0.0
            else:
                src = f"[{i}:a]"
                # 逐段量响度 → 静态增益（参数与理由见 AUDIO_TARGET_LUFS 处）
                gain = _loudness_gain_db(await probe_loudness(inputs[i]))
            gains.append(gain)
            # 增益小于 0.1 dB 就不加滤镜（省得噪声一样的 filter 串）
            pre = f"volume={gain:.2f}dB," if abs(gain) >= 0.1 else ""
            filter_parts.append(
                f"{src}{pre}aresample=44100,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[ai{i}]"
            )
            audio_labels.append(f"[ai{i}]")
        if any(abs(g) >= 0.1 for g in gains):
            logger.info("拼接前逐段音量归一化（目标 %.1f LUFS）：%s dB",
                        AUDIO_TARGET_LUFS, [round(g, 1) for g in gains])
        for i in range(n - 1):
            src_left = audio_labels[0] if i == 0 else f"[a{i}]"
            out_label = "[aout]" if i == n - 2 else f"[a{i+1}]"
            filter_parts.append(
                f"{src_left}{audio_labels[i+1]}"
                f"acrossfade=d={TRANSITION}:c1=tri:c2=tri{out_label}"
            )

    filter_complex = ";".join(filter_parts)
    cmd = [
        ffmpeg_exe(), "-y",
        *inputs_args,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
    ]
    if with_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    cmd += ["-movflags", "+faststart", str(output)]
    logger.info("xfade cmd: %s", " ".join(cmd[:5]) + " ... filter_complex=" + filter_complex[:200])
    res = await run_command(cmd, timeout=1200)
    if res.timed_out:
        logger.error("xfade 超时")
        return False
    if res.returncode != 0:
        logger.warning("xfade 失败 rc=%s stderr=%s", res.returncode, res.stderr[-300:])
        return False
    if output.exists() and output.stat().st_size > 0:
        if with_audio and not await probe_has_audio(output):
            # 拼接"成功"但没有音轨 = 又回到那个 bug，必须当失败（让上层降级/告警）
            logger.error("xfade 产物没有音轨（输入有音轨），判定失败")
            return False
        return True
    return False


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
