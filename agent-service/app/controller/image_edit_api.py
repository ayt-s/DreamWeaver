"""图像定点修正 API：`POST /v1/images/edit`。

## 这是什么（为什么值得单独一个端点）

对**已有的一张图**做定向修改，而不是重新生成一张：官方图片接口支持图生图
（`extra_body.image`，实测返回 `/images/i2i/` 路径）。实测结论（2026-09-18）：

- **删/换局部 → 完全达标**：底图「两头黑牛」+ 指令「只保留一头，其余不变」
  → 结果只剩一头，且人物/服装/姿势/场景/光线/构图全部保持
- **改景别 → 只达标一半**：指令「拉远为中远景」→ 景别真的变远，**但人物外观也被重画**

所以本端点定位是「**局部修正**」（删掉多出来的主体、换掉多余道具），
不是改构图 —— 前端文案要写清这一点，否则用户会拿它去治「大脸」然后得到一张换了人的图。

另外两轮实测表明：i2i **对「锁脸」没有优势**（与详细文字描述相比无可见差异），
所以不要把它当角色一致性手段用。

## 为什么前端直连 agent（不经 Java）

与 `POST /v1/qc/images` 同一先例：修正结果是画布节点上的候选，**不该**创建
`creative_task` 记录（否则一次修正就在画廊/草稿区刷出一个任务）；也不该落库 ——
`result_json` / `image_urls` 都被 Java 当 URL 数组解析，塞不进对象。
"""
from __future__ import annotations

import base64
import logging
from urllib.parse import urlparse

import httpx
import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings
from app.errors import AppError
from app.gateway.agnes import gateway
from app.utils.prompting import normalize_image_ratio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/images", tags=["images"])

# 一次最多出几张修正结果（取 1~2：修正讲「改对」，不是「多挑」）
MAX_COUNT = 2
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_DOWNLOAD_TIMEOUT = 60.0

# 「补画幅」（扩画幅）预设：把当前图的比例补齐到目标画幅。
#
# 为什么需要（2026-09-18 实测）：老项目（39/40）的图是 1:1（1024x1024，出图比例修复前
# 生成的），而 keyframe 视频的几何**跟随首帧比例** → 那些段的视频是 704x704 方的。
# 探针已证实两点：① agnes 接受 **data URI** 当首帧（补边后的图能直接喂视频接口）；
# ② i2i 能把「生硬补出来的边」画成场景的自然延续。所以不必让用户重出图（那会刷掉
# 他已认可的图），本地补边 + i2i 扩画幅即可得到真正可用的宽屏首帧。
#
# 补边用 replicate（最差的镜像拉伸）只作为 i2i 的**输入**：模型会把两侧重画成连续场景。
# ⚠️ 实测代价：模型会顺带把中间**重新构图为更宽的景别**（人物/场景/动作都在，但脸变小）。
# 所以这是一个「愿意接受重新构图」才用的入口，不是无副作用的比例转换。
OUTPAINT_INSTRUCTION = (
    "这张图是宽画幅，但左右两侧是后期补出来的拉伸区域。请把**两侧**补画成与中间画面"
    "完全连续的场景延伸（同一地点、同一光线、同一天气、同一画风、相同的景深与颗粒感），"
    "使整张图看起来本来就是在这个画幅里拍摄的。不要出现拼接痕迹或镜像重复。"
)
# 2K 档的画幅尺寸（16:9 → 2624x1472 为本项目实测值）；其余按长边 2624 推算
PAD_TARGETS = {
    "16:9": (2624, 1472),
    "9:16": (1472, 2624),
    "1:1": (2048, 2048),
    "4:3": (2624, 1968),
    "3:4": (1968, 2624),
}


class ImageEditRequest(BaseModel):
    image_url: str = Field(..., description="要修正的图（agnes 产物直链，或本地上传图的 URL）")
    instruction: str = Field("", max_length=800,
                             description="修改指令，如「只保留一头黑牛，其余不变」；"
                                         "用 pad_to_ratio 时留空即可")
    ratio: str | None = Field(None, description="输出画幅（默认与原图语境一致，可省略）")
    size: str | None = None
    count: int = Field(1, ge=1, le=MAX_COUNT)
    pad_to_ratio: str | None = Field(
        None,
        description="补画幅（扩画幅）：把这个画幅补齐 —— 本地补边 + i2i 让模型把两侧"
                    "画成场景延续。用于修「图是 1:1、视频跟着出方形」这类几何问题",
    )


def _is_local_url(url: str) -> bool:
    """本地/内网地址 —— agnes 拉不到，必须由我们转成 base64 再发。"""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return True
    if host.startswith("192.168.") or host.startswith("10."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


async def _load_image_bytes(url: str) -> tuple[bytes, str]:
    """取图片字节 → (bytes, mime)。支持 http(s) 与 data URI 两种来源。"""
    if url.startswith("data:"):
        head, _, payload = url.partition(",")
        mime = head[5:].split(";")[0] or "image/png"
        return base64.b64decode(payload), mime
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError(f"图片过大（{len(data) // 1024 // 1024}MB），请换小一点的图")
    mime = resp.headers.get("content-type", "").split(";")[0].strip() or "image/png"
    if not mime.startswith("image/"):
        mime = "image/png"
    return data, mime


def _absolute_url(image_url: str) -> str:
    """相对路径（`/api/uploads/x.png`）补上 Java 基址。"""
    raw = (image_url or "").strip()
    if not raw:
        raise AppError("缺少要修正的图片")
    if raw.startswith("/"):
        base = (settings.java_notify_url or "").rstrip("/")
        if not base:
            raise AppError("本地上传图需要 JAVA_NOTIFY_URL 才能取回，请先配置")
        return f"{base}{raw}"
    return raw


def _pad_to_ratio(data: bytes, ratio: str) -> tuple[bytes, float]:
    """把图片补边到目标画幅（不裁不拉伸），返回 (PNG 字节, 原图比例)。

    补边用 `BORDER_REPLICATE`（最差的镜像拉伸）—— 它只是 **i2i 的输入**，
    模型会把两侧重画成场景延续（实测有效）。所以这里不需要做得更聪明。
    """
    import cv2  # 局部 import：只用在这里，且 opencv 是重依赖（QC 已依赖它）

    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise AppError("读不出这张图（可能不是图片格式）")
    h, w = arr.shape[:2]
    if w <= 0 or h <= 0:
        raise AppError("图片尺寸异常")
    src_ratio = w / h
    tw, th = PAD_TARGETS.get(ratio, (2624, 1472))
    scale = min(tw / w, th / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_AREA)
    top, left = (th - nh) // 2, (tw - nw) // 2
    canvas = cv2.copyMakeBorder(resized, top, th - nh - top, left, tw - nw - left,
                                cv2.BORDER_REPLICATE)
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise AppError("补边后编码失败")
    return buf.tobytes(), src_ratio


async def _to_data_uri(url: str) -> str:
    """把本地图抓成 Data URI（官方明确支持）——本地上传图 agnes 拉不到。"""
    data, mime = await _load_image_bytes(url)
    return f"data:{mime};base64," + base64.b64encode(data).decode()


async def _resolve_reference(image_url: str) -> str:
    """把「用户给的图」变成 agnes 能收的输入。

    - 公网 URL → 原样透传（agnes 自己会拉）
    - 本地/内网 URL（本地上传图是 `http://localhost:8080/api/uploads/...`）→ 抓成 Data URI
    - 相对路径（如 `/api/uploads/x.png`）→ 补上 Java 的基址再抓
    """
    raw = _absolute_url(image_url)
    if _is_local_url(raw):
        return await _to_data_uri(raw)
    return raw


@router.post("/edit")
async def edit_image(req: ImageEditRequest) -> dict:
    """按指令对已有一张图做定点修正；`pad_to_ratio` 时改为「补画幅」（扩画幅）。"""
    instruction = req.instruction.strip()
    pad_ratio = normalize_image_ratio(req.pad_to_ratio, "") if req.pad_to_ratio else ""
    if not instruction and not pad_ratio:
        raise AppError("请写清要改什么")

    if pad_ratio:
        # 补画幅：本地补边 → 当 i2i 输入 → 让模型把两侧画成场景延续。
        # 源图**必须下载**（要改像素），所以这里不走「公网 URL 透传」那条捷径。
        data, _mime = await _load_image_bytes(_absolute_url(req.image_url))
        padded, src_ratio = _pad_to_ratio(data, pad_ratio)
        target = PAD_TARGETS.get(pad_ratio, (2624, 1472))
        target_ratio = target[0] / target[1]
        if abs(src_ratio - target_ratio) < 0.02:
            # 已是目标比例 → 别白跑一趟（模型会顺手把中间重新构图，是有代价的）
            raise AppError(f"这张图已经是 {pad_ratio} 比例，不需要补画幅")
        ref = "data:image/png;base64," + base64.b64encode(padded).decode()
        prompt = OUTPAINT_INSTRUCTION
        if instruction:
            prompt = f"{OUTPAINT_INSTRUCTION}另外：{instruction}"
        logger.info("补画幅：%s（源比例 %.3f → 目标 %s）", req.image_url[-60:], src_ratio, pad_ratio)
    else:
        ref = await _resolve_reference(req.image_url)
        prompt = instruction

    ratio = normalize_image_ratio(req.ratio, settings.default_aspect_ratio)

    urls: list[str] = []
    last_error: str | None = None
    for i in range(req.count):
        try:
            got = await gateway.generate_image(
                prompt=prompt,
                ratio=pad_ratio or ratio,
                size=req.size,
                reference_images=[ref],
                session_id=None,
            )
        except Exception as exc:  # noqa: BLE001 —— 单张失败不影响另一张
            last_error = str(exc).strip() or type(exc).__name__
            logger.warning("图像修正第 %d 张失败：%s", i + 1, last_error)
            continue
        urls.extend(got)

    if not urls:
        # 修正失败要**如实报错**（不像质检那样静默降级）：这是用户主动发起的操作，
        # 静默失败会让他以为「点了没反应」。
        raise AppError(f"修正失败：{last_error or '模型没有返回图片'}")

    logger.info("图像修正完成：%d 张 | %s | 指令=%.60s",
                len(urls), f"补画幅到 {pad_ratio}" if pad_ratio else "定点修正", prompt)
    return {"code": 0, "message": "ok", "data": {"urls": urls}}
