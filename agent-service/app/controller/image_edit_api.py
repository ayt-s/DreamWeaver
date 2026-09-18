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


class ImageEditRequest(BaseModel):
    image_url: str = Field(..., description="要修正的图（agnes 产物直链，或本地上传图的 URL）")
    instruction: str = Field(..., min_length=1, max_length=800,
                             description="修改指令，如「只保留一头黑牛，其余不变」")
    ratio: str | None = Field(None, description="输出画幅（默认与原图语境一致，可省略）")
    size: str | None = None
    count: int = Field(1, ge=1, le=MAX_COUNT)


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


async def _to_data_uri(url: str) -> str:
    """把本地图抓成 Data URI（官方明确支持）——本地上传图 agnes 拉不到。"""
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError(f"图片过大（{len(data) // 1024 // 1024}MB），请换小一点的图")
    mime = resp.headers.get("content-type", "").split(";")[0].strip() or "image/png"
    if not mime.startswith("image/"):
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


async def _resolve_reference(image_url: str) -> str:
    """把「用户给的图」变成 agnes 能收的输入。

    - 公网 URL → 原样透传（agnes 自己会拉）
    - 本地/内网 URL（本地上传图是 `http://localhost:8080/api/uploads/...`）→ 抓成 Data URI
    - 相对路径（如 `/api/uploads/x.png`）→ 补上 Java 的基址再抓
    """
    raw = (image_url or "").strip()
    if not raw:
        raise AppError("缺少要修正的图片")
    if raw.startswith("/"):
        base = (settings.java_notify_url or "").rstrip("/")
        if not base:
            raise AppError("本地上传图需要 JAVA_NOTIFY_URL 才能取回，请先配置")
        raw = f"{base}{raw}"
    if _is_local_url(raw):
        return await _to_data_uri(raw)
    return raw


@router.post("/edit")
async def edit_image(req: ImageEditRequest) -> dict:
    """按指令对已有一张图做定点修正，返回新图 URL 列表。"""
    instruction = req.instruction.strip()
    if not instruction:
        raise AppError("请写清要改什么")

    ref = await _resolve_reference(req.image_url)
    ratio = normalize_image_ratio(req.ratio, settings.default_aspect_ratio)

    urls: list[str] = []
    last_error: str | None = None
    for i in range(req.count):
        try:
            got = await gateway.generate_image(
                prompt=instruction,
                ratio=ratio,
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

    logger.info("图像修正完成：%d 张 | 指令=%.60s", len(urls), instruction)
    return {"code": 0, "message": "ok", "data": {"urls": urls}}
