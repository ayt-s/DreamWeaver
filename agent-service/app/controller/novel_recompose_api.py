"""存量画布提示词重算 API：`POST /v1/novel/recompose-prompts`。

## 为什么是 agent 侧端点，而不是改 Java controller

规则（动物移出角色锚 / 角色别名 / 镜头清理 / 场景追加）**只存在于 agent 的
`app/novel/composer.py`**。放进 Java 只有两条路：把规则复制一份到 Java（两处实现，
必然漂移，这个入口的意义就是消除漂移），或让 Java 反调 Python（多一跳）。
纯函数端点放 agent 侧，规则单一出处；Java 与 DB 一行都不用改，也不新增契约。

## 契约

请求：`{analysis: <object|null>, nodes: [{id, prompt}]}`
响应：`{code: 0, message: "ok", data: {nodes: [{id, prompt, changed, skipped, reasons}],
       changed_count, animal_source, recompute_camera}}`

**纯函数**：不碰 DB、不写 Java、不发网络请求。落库由前端走既有乐观锁 PUT
（`/api/canvas/{id}`，带 `version`）+ 回读确认完成 —— 因为这个端点的使用者
（画布页）本来就持有 `version`，在它那里落库才能沿用同一套并发保护。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.novel.prompt_recompose import recompose_prompts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/novel", tags=["novel"])


class RecomposeNode(BaseModel):
    id: str = Field(..., description="画布节点 id")
    prompt: str = Field(default="", description="该节点当前的提示词")


class RecomposeRequest(BaseModel):
    # 画布的 analysisJson（analyzer 结构化产物）。拿不到就传 null → 退回关键词表，
    # 响应里的 animal_source 会如实说明用的是哪一种判定来源。
    analysis: Any = Field(default=None, description="分析结果对象或 JSON 字符串；可为 null")
    nodes: list[RecomposeNode] = Field(default_factory=list, description="要重算的节点（其余节点不用传）")


class RecomposeResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict


@router.post("/recompose-prompts", response_model=RecomposeResponse)
async def recompose_prompts_api(req: RecomposeRequest) -> RecomposeResponse:
    """按**当前** composer 规则重算提示词（dry-run 友好：调用方拿结果做确认，落库自己决定）。"""
    data = recompose_prompts(req.analysis, [n.model_dump() for n in req.nodes])
    return RecomposeResponse(code=0, message="ok", data=data)
