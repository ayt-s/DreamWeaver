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
from app.tools.subject_count import SUBJECT_QUESTION, parse_subject_answer, subject_label

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


# ------------------------- 候选主体计数（多模态，2026-09-18 标定 13/13） -------------------------

# 一次最多几张：一张约 46s（下载 5MB 原图 + 模型带推理读图），6 张已接近前端能等的上限。
MAX_CANDIDATES = 6
# 并发上限：串行 3 张实测 139s 太慢；全并发怕撞限流（未实测），取中间值。
_CANDIDATE_CONCURRENCY = 2
# 送进模型前先把图缩到长边 1024（产物是 2624x1472 PNG ≈5MB，base64 太大）
_MAX_SIDE = 1024


class CandidateQcRequest(BaseModel):
    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_CANDIDATES,
        description="要数主体（人 / 动物）的候选图 URL",
    )


def _to_data_url(data: bytes) -> str | None:
    """缩放成宽 ≤1024 的 JPEG data URL —— 实测缩完照样数得准，且体积降到 ~150KB。"""
    import base64

    import cv2
    import numpy as np

    im = cv2.imdecode(np.frombuffer(data, dtype="uint8"), cv2.IMREAD_COLOR)
    if im is None:
        return None
    h, w = im.shape[:2]
    if w > _MAX_SIDE:
        im = cv2.resize(im, (_MAX_SIDE, int(h * _MAX_SIDE / w)))
    ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


async def _count_one(url: str) -> dict:
    """单张图的主体计数。**任何失败都降级成 skipped**，不冒泡（质检是附加信息）。"""
    from app.gateway.agnes import gateway

    item: dict = {"index": 0, "url": url, "skipped": True, "reason": "",
                  "people": None, "animals": None, "faceCloseup": None, "label": ""}
    try:
        data = await _fetch_bytes(url)
    except Exception as exc:  # noqa: BLE001
        logger.info("主体计数下载失败 %s: %s", url, exc)
        item["reason"] = "图片下载失败"
        return item
    data_url = _to_data_url(data)
    if not data_url:
        item["reason"] = "图片解码失败"
        return item
    try:
        text = await gateway.chat_with_images(SUBJECT_QUESTION, [data_url])
    except Exception as exc:  # noqa: BLE001
        logger.info("主体计数调用失败 %s: %s", url, exc)
        item["reason"] = "模型调用失败"
        return item
    verdict = parse_subject_answer(text)
    if verdict is None:
        logger.info("主体计数解析失败 %r", str(text)[:120])
        item["reason"] = "模型回答里没有可解析的 JSON"
        return item
    return {"index": 0, "url": url, "skipped": False, "reason": "",
            "label": subject_label(verdict), **verdict}


@router.post("/candidates")
async def qc_candidates(req: CandidateQcRequest) -> dict:
    """逐张数「几个人 / 几头动物」，供候选缩略图挂角标（如 `1人1牛`）。

    ⚠️ 与 `/images` 的**分工**（刻意不合并）：
    - `/images` = 本地 YuNet 判「面部特写」，确定性、一次出全部结论、几秒内
    - `/candidates` = 多模态模型数主体，慢（串行）、有调用成本，**角标晚到也没关系**

    价值在项目已确认的事实上：多出主体是**模型侧随机**（三张候选会一起错），
    候选缩略图上的数字是唯一能让用户一眼挑出来的东西（标定 13/13 一致）。

    ⚠️ **慢**：实测 3 张串行要 139s（每张约 46s —— 下载 5MB 原图 + 模型带推理读图）。
    所以这里**并发 2**（串行太慢、全并发怕撞限流），并且前端是异步 query：
    候选缩略图先出，数字晚到，不阻塞任何操作。
    """
    sem = asyncio.Semaphore(_CANDIDATE_CONCURRENCY)

    async def _run(idx: int, url: str) -> dict:
        async with sem:
            item = await _count_one(url)
        item["index"] = idx      # 序号按**入参位置**，与下载/调用失败无关
        return item

    results = list(await asyncio.gather(*(_run(i, u) for i, u in enumerate(req.urls))))
    counted = sum(1 for r in results if not r["skipped"])
    logger.info("候选主体计数：%s 张，成功 %s 张", len(results), counted)
    return {"code": 0, "message": "ok",
            "data": {"results": results, "summary": {"total": len(results), "counted": counted}}}
