"""小说转漫剧预处理 API：POST /v1/novel/preprocess。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.errors import AppError, friendly_error_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/novel", tags=["novel"])

MIN_NOVEL_LEN = 100


class NovelPreprocessRequest(BaseModel):
    novel_text: str = Field(..., description="小说原文")
    target_segments: int = Field(default=6, ge=1, le=30, description="期望分镜数")
    seconds_per_segment: int = Field(default=5, ge=4, le=12, description="单段目标秒数（会被 clamp 到 [4,12]）")
    style: str = Field(default="电影写实", description="整体视觉风格短语")
    generate_character_portrait: bool = Field(default=False, description="是否生成角色立绘（当前管线未启用）")


class NovelSegmentResponse(BaseModel):
    id: str
    chapter: int
    title: str
    plot: str
    characters: list[str]
    scene: str
    camera: str
    seconds: int
    mood: str
    imagePrompt: str
    videoPrompt: str


class NovelPreprocessResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict


@router.post("/preprocess", response_model=NovelPreprocessResponse)
async def preprocess(req: NovelPreprocessRequest) -> NovelPreprocessResponse:
    """小说 → 分镜结构化产物。"""
    text = (req.novel_text or "").strip()
    if len(text) < MIN_NOVEL_LEN:
        raise AppError(
            f"小说原文过短（当前 {len(text)} 字，至少需要 {MIN_NOVEL_LEN} 字）",
            status_code=422,
        )

    from app.novel.orchestrator import preprocess_novel

    try:
        data = await preprocess_novel(
            novel_text=text,
            target_segments=req.target_segments,
            seconds_per_segment=req.seconds_per_segment,
            style=req.style,
            generate_character_portrait=req.generate_character_portrait,
        )
    except Exception as exc:
        logger.error("小说预处理失败: %s", exc, exc_info=True)
        raise AppError(
            friendly_error_message(exc),
            status_code=500,
            detail=str(exc),
            retryable=True,
        )

    return NovelPreprocessResponse(code=0, message="ok", data=data)
