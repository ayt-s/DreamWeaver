"""Chat Agent FastAPI 路由：POST /v1/agent/chat。

请求：
    POST /v1/agent/chat
    {
      "canvas_id": 2,
      "message": "帮我把 vid2 的提示词改得更动态一点",
      "history": [  # 可选，多轮对话
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."}
      ]
    }

响应：
    {
      "code": 0,
      "message": "ok",
      "data": {
        "reply": "agent 的回复文本",
        "tool_calls": [ ... ],   # agent 调用了哪些工具（前端可展示 trace）
        "canvas_id": 2
      }
    }
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.errors import AppError, friendly_error_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/agent", tags=["agent"])


class ChatRequest(BaseModel):
    canvas_id: Optional[int] = Field(default=None, description="当前画布项目 id")
    message: str = Field(..., description="用户消息")
    history: list[dict] = Field(default_factory=list, description="可选：历史对话")


class ToolCallRecord(BaseModel):
    tool_name: str
    args: dict
    result: dict
    status: str  # ok / error


class ChatResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """调用 Pydantic AI agent，返回文本 + 工具调用轨迹。"""
    if not req.message.strip():
        raise AppError("message 不能为空", status_code=422)

    from app.agent.chat_agent import chat_agent

    # 把当前画布 id 放到 prompt 上下文里，agent 可以直接引用
    context = f"当前画布项目 id: {req.canvas_id}" if req.canvas_id else "未指定画布项目 id（请先问用户）"

    # 构造完整用户 prompt：历史对话 + 系统上下文 + 本轮消息
    # Pydantic AI 的 agent.run() 直接接受字符串 prompt，会把系统提示 + 历史 + 本轮合成完整上下文
    history_text = ""
    if req.history:
        parts = []
        for m in req.history:
            role = m.get("role", "user")
            content = m.get("content", "")
            prefix = "用户" if role == "user" else "助手"
            parts.append(f"[历史-{prefix}] {content}")
        history_text = "\n".join(parts) + "\n\n"

    full_prompt = f"{context}\n{history_text}用户消息：{req.message}"

    try:
        result = await chat_agent.run(full_prompt)
    except Exception as exc:
        logger.error("Agent 调用失败: %s", exc, exc_info=True)
        # 原始异常只进日志；用户看到的是中文友好映射（与全局异常层同一规范）
        raise AppError(
            friendly_error_message(exc),
            status_code=500,
            detail=str(exc),
            retryable=True,
        )

    # 提取工具调用轨迹（Pydantic AI 2.39: kind="response" 的 msg.parts 里有 ToolCallPart）
    tool_calls: list[ToolCallRecord] = []
    try:
        from pydantic_ai.messages import ToolCallPart, ToolReturnPart
        for msg in result.all_messages():
            if getattr(msg, "kind", None) != "response":
                continue
            for part in getattr(msg, "parts", []) or []:
                if isinstance(part, ToolCallPart):
                    tool_calls.append(ToolCallRecord(
                        tool_name=part.tool_name,
                        args=dict(part.args) if hasattr(part.args, "keys") else {},
                        result={},
                        status="called",
                    ))
    except Exception as exc:  # 解析失败不阻塞主流程
        logger.warning("解析 tool calls 失败: %s", exc)

    return ChatResponse(
        code=0,
        message="ok",
        data={
            "reply": result.output,
            "tool_calls": [tc.model_dump() for tc in tool_calls],
            "canvas_id": req.canvas_id,
            "model": chat_agent._model.model_name if hasattr(chat_agent, "_model") else "unknown",
        },
    )


class EnrichRequest(BaseModel):
    """提示词增强请求。gen_type: text_image / text_video。"""

    prompt: str = Field(..., description="用户原始描述")
    gen_type: str = Field(default="text_video", description="text_image 文生图 / text_video 文生视频")


class EnrichResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict


# 按生成类型定制的提示词增强系统提示词
_ENRICH_SYSTEM: dict[str, str] = {
    "text_image": (
        "你是一位专业的 AI 绘画提示词工程师。请把用户简短的中文描述扩展成一段高质量的中文文生图提示词。"
        "要求：保留用户原意，补充主体特征、场景环境、光影色调、镜头视角、风格流派、质感细节；"
        "用自然流畅的中文描述，不要使用逗号堆砌的英文标签格式，不要输出解释或前后缀，只输出提示词正文。"
        "控制在 100-200 字。"
    ),
    "text_video": (
        "你是一位专业的 AI 视频提示词工程师。请把用户简短的中文描述扩展成一段高质量的中文文生视频提示词。"
        "要求：保留用户原意，补充画面主体及其动作、场景环境、镜头运动（推拉摇移跟）、光影氛围、风格质感、"
        "时间节奏（如开场/高潮/结尾的镜头感）；用自然流畅的中文描述，不要输出解释或前后缀，只输出提示词正文。"
        "控制在 150-300 字。"
    ),
}


@router.post("/enrich-prompt", response_model=EnrichResponse)
async def enrich_prompt(req: EnrichRequest) -> EnrichResponse:
    """AI 丰富提示词：把用户简短描述扩展成适合文生图/文生视频的高质量提示词。"""
    if not req.prompt or not req.prompt.strip():
        raise AppError("请先输入创作描述", status_code=422)
    if req.gen_type not in _ENRICH_SYSTEM:
        raise AppError(f"不支持的生成类型: {req.gen_type}", status_code=422)

    from app.config import settings

    # 直连 agnes OpenAI 兼容端点（复用现有配置，不依赖 Pydantic AI 内部 API）
    try:
        import httpx

        resp = await httpx.AsyncClient(timeout=60).post(
            f"{settings.agnes_base_url.rstrip('/')}/chat/completions",
            headers=settings.headers,
            json={
                "model": settings.text_model,
                "messages": [
                    {"role": "system", "content": _ENRICH_SYSTEM[req.gen_type]},
                    {"role": "user", "content": req.prompt.strip()},
                ],
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        enriched = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:
        logger.error("提示词增强失败: %s", exc, exc_info=True)
        raise AppError(
            friendly_error_message(exc),
            status_code=500,
            detail=str(exc),
            retryable=True,
        )

    if not enriched:
        raise AppError("AI 未能生成提示词，请重试", status_code=500)

    return EnrichResponse(
        code=0,
        message="ok",
        data={"prompt": enriched, "gen_type": req.gen_type},
    )
