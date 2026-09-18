"""小说角色/场景锚定图 API：POST /v1/novel/anchors。

用途：Java 转画布前调用，为每个角色和场景生成一张锚定参考图（4K、电影感）。
返回结果：{characters: {name: url}, scenes: {name: url}}

调用一次生成 3-5 个角色 + 3-5 个场景 = 6-10 张图片，每张约 15-25s，
整体约 2-3 分钟。前端 NovelPage 转画布时自动调用。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.errors import AppError
from app.utils.prompting import strip_subject_clauses

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/novel", tags=["novel"])

_CHARACTER_PROMPT_TEMPLATE = (
    "电影级人像摄影，{style} 视觉风格，{character_name}，{character_desc}。"
    "中景正面像，自然光，背景简洁，浅景深聚焦角色面部与服装细节。"
    "严禁面部特写大特写；主体清晰居中；16:9 画幅；4K 超高清；"
    "无字幕无水印；无文字乱码；周围留出安全边距。"
)

_SCENE_PROMPT_TEMPLATE = (
    "电影级场景摄影，{style} 视觉风格，{scene_desc}。"
    # ★ 2026-09-19：原来只有「空场景无角色」，但**抽象约束挡不住具体描述** ——
    #   实测废墟锚图仍然画了「一人一牛」（因为场景描述原文里就写着这句）。
    #   真正的修法是把描述里的主体子句剥掉（见 `strip_subject_clauses`），
    #   这里的措辞再加强一层，作为第二道防线。
    "空场景无角色：画面中不得出现任何人、动物、牲畜、人影或身体局部，只有地点与环境本身。"
    "广角构图，自然光线，气氛感强。"
    "主体清晰居中；16:9 画幅；4K 超高清；"
    "无字幕无水印；无文字乱码；周围留出安全边距。"
)


class NovelAnchorsRequest(BaseModel):
    characters: dict[str, str] = Field(
        default_factory=dict,
        description="角色字典：{name: 特征卡}",
    )
    scenes: list[str] = Field(
        default_factory=list,
        description="场景描述列表",
    )
    style: str = Field(default="电影写实", description="整体视觉风格短语")
    max_per_type: int = Field(default=5, ge=1, le=10, description="每类最多生成几张")


class NovelAnchorsResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict


async def _generate_one_character(name: str, desc: str, style: str) -> tuple[str, str] | None:
    """生成一张角色锚定图。返回 (name, url) 或 None（失败时）。"""
    from app.gateway.agnes import gateway

    prompt = _CHARACTER_PROMPT_TEMPLATE.format(
        style=style, character_name=name, character_desc=desc,
    )
    try:
        urls = await gateway.generate_image(prompt)
        return (name, urls[0]) if urls else None
    except Exception as e:
        logger.warning("角色锚定图生成失败 %s: %s", name, e)
        return None


async def _generate_one_scene(desc: str, style: str,
                              names: tuple[str, ...] = ()) -> tuple[str, str] | None:
    """生成一张场景锚定图。返回 (scene_desc, url) 或 None。

    ★ 2026-09-19：喂给模型的描述要**剥掉主体子句**（`strip_subject_clauses`）——
      场景描述里常写着「一人一牛跪坐门外」，模型就照画，锚图于是带着人和牛；
      这张锚图之后当参考图喂给每镜首帧，模型又把主体**再画一遍**
      （实测：废墟镜带锚 人多 4/5 vs 不带 0/10，而山洞锚图是空景 → 该镜从不多画）。

    ⚠️ 返回的 key 必须是**原描述**（不是剥过的）：面板与画布用原描述做 key
      （前端 `json.data.scenes[description]` 就是拿原描述取的），改了 key 就取不到图。
    """
    from app.gateway.agnes import gateway

    clean = strip_subject_clauses(desc, names)
    if clean != desc:
        logger.info("场景锚定图描述已剥主体子句: %s → %s", desc[:40], clean[:40])
    prompt = _SCENE_PROMPT_TEMPLATE.format(style=style, scene_desc=clean)
    try:
        urls = await gateway.generate_image(prompt)
        return (desc, urls[0]) if urls else None
    except Exception as e:
        logger.warning("场景锚定图生成失败 %s: %s", desc[:30], e)
        return None


@router.post("/anchors", response_model=NovelAnchorsResponse)
async def generate_anchors(req: NovelAnchorsRequest) -> NovelAnchorsResponse:
    """为角色和场景批量生成锚定图。"""
    character_items = list(req.characters.items())[: req.max_per_type]
    scene_items = req.scenes[: req.max_per_type]

    if not character_items and not scene_items:
        raise AppError("characters 和 scenes 至少提供一个", status_code=422)

    # 并发提交：agnès 图片 API 无严格并发限制，一次并发所有
    tasks: list[asyncio.Task] = []
    for name, desc in character_items:
        tasks.append(asyncio.create_task(_generate_one_character(name, desc, req.style)))
    for desc in scene_items:
        # 角色名传进去：场景描述里点名角色的子句也一并剥掉
        tasks.append(asyncio.create_task(
            _generate_one_scene(desc, req.style, tuple(req.characters.keys()))))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    characters: dict[str, str] = {}
    scenes: dict[str, str] = {}
    # 结果顺序跟任务顺序一致：前 len(character_items) 个是 characters，后面是 scenes
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            logger.warning("锚定图任务失败 idx=%d: %s", i, r)
            continue
        if r is None:
            continue
        if i < len(character_items):
            characters[r[0]] = r[1]
        else:
            scenes[r[0]] = r[1]

    logger.info(
        "锚定图生成完成: characters=%d/%d scenes=%d/%d",
        len(characters), len(character_items), len(scenes), len(scene_items),
    )
    return NovelAnchorsResponse(
        code=0,
        message="ok",
        data={"characters": characters, "scenes": scenes},
    )
