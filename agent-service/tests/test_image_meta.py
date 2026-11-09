"""首帧真实画幅探测（`app/utils/image_meta.py`）与 storyboard 的校正逻辑。

★ 2026-09-24 真机实测的由来：keyframe 模式下 agnes **输出画幅跟随首帧图**。
画布节点声明 `16:9`，首帧却是 1024×1024 → 成片 704×704 方视频：
- 提示词第一句写着 `16:9 horizontal`，与成片自相矛盾；
- QC 按声明的 16:9 判「画幅不符」，把「期望值错了」报成「模型错了」。

所以提交前用首帧真实像素算画幅，提示词与 QC 期望都跟着首帧走。
"""

from __future__ import annotations

import struct

import pytest

from app.utils.image_meta import nearest_ratio, size_from_bytes


def _png(w: int, h: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR"
            + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00")


def _gif(w: int, h: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00\x00\x00"


def _jpeg(w: int, h: int) -> bytes:
    # SOI + APP0(长度 16) + SOF0(长度 17, 精度 8, 高, 宽)
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x03" + b"\x00" * 9
    return b"\xff\xd8" + app0 + sof


def _webp_vp8x(w: int, h: int) -> bytes:
    # VP8X 载荷 = 1 字节 flags + 3 字节 reserved + 3 字节(宽-1) + 3 字节(高-1)
    head = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8X" + b"\x0a\x00\x00\x00"
    payload = b"\x00" + b"\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return head + payload


@pytest.mark.parametrize("blob,expect", [
    (_png(2624, 1472), (2624, 1472)),
    (_png(1024, 1024), (1024, 1024)),
    (_jpeg(1920, 1080), (1920, 1080)),
    (_gif(640, 480), (640, 480)),
    (_webp_vp8x(1600, 900), (1600, 900)),
])
def test_size_from_bytes_parses_common_formats(blob, expect):
    assert size_from_bytes(blob) == expect


def test_size_from_bytes_rejects_non_image():
    assert size_from_bytes(b"<html>not an image</html>") is None
    assert size_from_bytes(b"") is None


@pytest.mark.parametrize("w,h,ratio", [
    (2624, 1472, "16:9"),   # agnes 出图的真实尺寸（比值 1.7826）
    (1280, 704, "16:9"),    # agnes 视频输出（1.818，容差内）
    (1024, 1024, "1:1"),
    (704, 1280, "9:16"),
    # 1.287 落在 4:3 的 6% 容差内 → 归到 4:3（真实图片常有裁切，不该动辄报怪比例）
    (1000, 777, "4:3"),
    (2000, 1000, "2000:1000"),  # 2:1 不在标准表里 → 原样给出
])
def test_nearest_ratio(w, h, ratio):
    assert nearest_ratio(w, h) == ratio


@pytest.mark.asyncio
async def test_canvas_ratio_follows_first_frame(monkeypatch):
    """首帧是方图时，段画幅必须改成 1:1（否则提示词/QC 与成片打架）。"""
    from app.nodes import storyboard as sb

    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        return ("5 seconds, 16:9 horizontal. 0-2s: x. 2-5s: y. Ambient wind. "
                "No text or watermark.")

    async def fake_probe(url, **kwargs):
        return "1:1"

    monkeypatch.setattr(sb.gateway, "chat", fake_chat)
    monkeypatch.setattr(sb, "probe_ratio", fake_probe)

    out = await sb.canvas_storyboarder_node({
        "session_id": "ratio-1",
        "segments": [{
            "image_url": "https://example.com/square.png",
            "prompt": "陈浔蹲在灶前拨火", "seconds": 5, "aspect_ratio": "16:9",
        }],
    })
    shot = out["storyboard"][0]
    assert shot["aspect_ratio"] == "1:1", "keyframe 输出跟随首帧，画幅必须跟着改"


@pytest.mark.asyncio
async def test_canvas_ratio_kept_when_probe_fails(monkeypatch):
    """探测失败（网络/格式不支持）必须回落到声明画幅，不能把段弄坏。"""
    from app.nodes import storyboard as sb

    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        return ("5 seconds, 16:9 horizontal. 0-2s: x. 2-5s: y. Ambient wind. "
                "No text or watermark.")

    async def fake_probe(url, **kwargs):
        return None

    monkeypatch.setattr(sb.gateway, "chat", fake_chat)
    monkeypatch.setattr(sb, "probe_ratio", fake_probe)

    out = await sb.canvas_storyboarder_node({
        "session_id": "ratio-2",
        "segments": [{
            "image_url": "https://example.com/x.png",
            "prompt": "陈浔蹲在灶前拨火", "seconds": 5, "aspect_ratio": "16:9",
        }],
    })
    assert out["storyboard"][0]["aspect_ratio"] == "16:9"


def test_ratio_head_wording():
    """1:1 要写成 square（模型读得懂），别只给数字。"""
    from app.utils.prompting import build_video_rewrite_input

    text = build_video_rewrite_input("陈浔蹲在灶前拨火", 5, "1:1")
    # 画幅头在模板里是**被引号括起的输入规格**（模型照它写正文第一句）
    assert '5 seconds, 1:1 square' in text
