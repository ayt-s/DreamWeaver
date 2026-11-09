"""读远端图片的**真实像素尺寸**（零依赖，只解析文件头）。

为什么需要
----------
keyframe 模式下 agnes 的**输出画幅跟随首帧图**，而提示词第一句写的是分镜声明的画幅。
两者不一致时会有两个坏结果（2026-09-24 真机实测）：

1. 模型收到一对矛盾指令：`5 seconds, 16:9 horizontal.` + 一张 1024×1024 的方图 →
   产出 704×704 方视频，「提示词说的画幅」和「成片」自相矛盾；
2. QC 按分镜声明的 16:9 判「画幅不符」，报出来的原因看似是模型的错，实际是期望值错了。

所以提交前先用首帧的真实尺寸算出画幅：提示词与 QC 期望都跟着首帧走。

为什么不用 Pillow
-----------------
项目依赖里没有 Pillow，而这里只需要宽高 —— PNG/JPEG/WebP/GIF 的文件头几十行就能解析，
不值得为它加一个二进制依赖。
"""

from __future__ import annotations

import struct

import httpx

__all__ = ["size_from_bytes", "nearest_ratio", "probe_ratio", "probe_size"]

# 只取文件头：足够解析尺寸，避免为一个宽高下载整张图
_MAX_HEAD = 65536
# 常见画幅（按数值升序），用于把 2624×1472 这类真实尺寸映射回 16:9
_RATIOS: tuple[tuple[str, float], ...] = (
    ("9:16", 9 / 16),
    ("2:3", 2 / 3),
    ("3:4", 3 / 4),
    ("1:1", 1.0),
    ("4:3", 4 / 3),
    ("3:2", 3 / 2),
    ("16:9", 16 / 9),
    ("21:9", 21 / 9),
)
# 偏差超过 6% 就不硬套标准画幅（否则 1.5 会被说成 16:9）
_RATIO_TOLERANCE = 0.06

# url → (w, h)：一次任务里同一张首帧会被反复问（改写 + QC），别重复下载
_CACHE: dict[str, tuple[int, int] | None] = {}


def size_from_bytes(data: bytes) -> tuple[int, int] | None:
    """从图片字节里解析 (宽, 高)；认不出返回 None。支持 PNG / JPEG / WebP / GIF。"""
    if not data:
        return None
    try:
        # --- PNG：8 字节签名 + 4 字节长度 + 'IHDR' + 宽高（big-endian） ---
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)

        # --- GIF：GIF87a/GIF89a + 逻辑屏宽高（little-endian） ---
        if data[:6] in (b"GIF87a", b"GIF89a"):
            w, h = struct.unpack("<HH", data[6:10])
            return int(w), int(h)

        # --- WebP：RIFF....WEBP，分 VP8X / VP8L / VP8 三种块头 ---
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                # 24 位宽高（减 1），从 24 字节处开始
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return w, h
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 ":
                # 关键帧起始码 9d 01 2a 之后的 2+2 字节
                idx = data.find(b"\x9d\x01\x2a", 20)
                if idx > 0:
                    w, h = struct.unpack("<HH", data[idx + 3: idx + 7])
                    return int(w) & 0x3FFF, int(h) & 0x3FFF

        # --- JPEG：逐段找 SOF（0xC0~0xCF，排除 C4/C8/CC） ---
        if data[:2] == b"\xff\xd8":
            i = 2
            n = len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5: i + 9])
                    return int(w), int(h)
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = struct.unpack(">H", data[i + 2: i + 4])[0]
                i += 2 + seg_len
    except (struct.error, IndexError, ValueError):
        return None
    return None


def nearest_ratio(width: int, height: int) -> str:
    """把像素尺寸映射成画幅字符串（16:9 / 9:16 / 1:1 …）；套不上标准画幅就返回 `w:h`。"""
    if width <= 0 or height <= 0:
        return ""
    actual = width / height
    best = min(_RATIOS, key=lambda kv: abs(kv[1] - actual))
    if abs(best[1] - actual) / best[1] <= _RATIO_TOLERANCE:
        return best[0]
    return f"{width}:{height}"


async def probe_size(url: str, *, timeout: float = 10.0) -> tuple[int, int] | None:
    """下载图片头部并解析尺寸；失败返回 None（调用方必须能接受探测失败）。"""
    key = str(url or "").strip()
    if not key:
        return None
    if key in _CACHE:
        return _CACHE[key]
    size: tuple[int, int] | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.get(key)
            if resp.status_code == 200:
                size = size_from_bytes(resp.content[:_MAX_HEAD])
    except (httpx.HTTPError, OSError, ValueError):
        size = None
    _CACHE[key] = size
    return size


async def probe_ratio(url: str, *, timeout: float = 10.0) -> str | None:
    """首帧 URL → 真实画幅字符串；探测失败返回 None。"""
    size = await probe_size(url, timeout=timeout)
    if not size:
        return None
    ratio = nearest_ratio(*size)
    return ratio or None
