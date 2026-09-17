"""首帧质检 API：`POST /v1/qc/images`。

## 为什么是「按需端点」而不是写进回调/库

前端拿到候选图后**按需问一次**「这几张里哪张踩了『严禁面部特写』红线」：

- 不落 DB：`creative_task.result_json` 被 Java 的 `TaskJsonCodec.parseResultUrls` 当 **URL 数组**
  解析、`image_urls` 同理、`gen_params_json` 会被 `applyGenParamsJson` 还原成请求参数 ——
  三处都塞不了对象，加列则要动 schema 与 Java 契约（刻意避免）。
- 不动生成链路：出图时**不额外下载**（图片本来就在 agnes 上，谁要看谁才拉）。

质检失败一律降级成 `skipped=true`，**不返回 4xx/5xx 打断前端**（质检是附加信息）。
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.tools.image_qc import judge_image_bytes, summarize

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/qc", tags=["qc"])

# 一次最多质检几张：一张一两秒，再多前端等不起（也避免被当探测工具用）。
MAX_IMAGES = 8
# 单张图片的下载上限，防止超大图把内存吃掉。
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_DOWNLOAD_TIMEOUT = 30.0
_CONCURRENCY = 3


class ImageQcRequest(BaseModel):
    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_IMAGES,
        description="要质检的图片 URL（agnes 产物直链）",
    )


async def _fetch_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"图片过大：{len(data) // 1024 // 1024}MB")
    return data


async def _judge_one(url: str, sem: asyncio.Semaphore) -> dict:
    """单张图的质检结果。**任何失败都降级成 skipped**，不冒泡。"""
    async with sem:
        try:
            data = await _fetch_bytes(url)
        except Exception as exc:  # noqa: BLE001
            logger.info("质检图下载失败 %s: %s", url, exc)
            return {"url": url, "skipped": True, "reason": "图片下载失败",
                    "closeup": False, "faces": 0, "faceSpan": 0.0}
        verdict = judge_image_bytes(data)
        return {"url": url, **verdict}


@router.post("/images")
async def qc_images(req: ImageQcRequest) -> dict:
    """按 URL 列表逐张判定「是否面部特写」，并给出建议选哪一张。"""
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(*(_judge_one(u, sem) for u in req.urls))
    payload = list(results)
    summary = summarize(payload)
    # 前端要用 index 反查候选，这里显式带上序号（别依赖数组位置，下载失败也不会错位）
    for i, item in enumerate(payload):
        item["index"] = i
    logger.info("图像质检：%s 张，其中 %s 张疑似面部特写", summary["total"], summary["closeupCount"])
    return {"code": 0, "message": "ok", "data": {"results": payload, "summary": summary}}
