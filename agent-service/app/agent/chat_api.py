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
    history: list[dict] = Field(default_factory=list, description="可选：历史对话（仅服务端无真历史时使用）")
    conversation_id: Optional[str] = Field(
        default=None,
        description="多轮对话 id（前端按画布生成的稳定 id）。给了它就由服务端保管结构化历史；"
                    "不给则退化成「前端拍平文本历史」的无状态单轮。",
    )


class ToolCallRecord(BaseModel):
    tool_name: str
    args: dict
    result: str = ""
    status: str  # ok / error / called（called = 模型调了但没看到返回值，理论上不该出现）
    #: result 是否被截断（工具返回值可能很大，如 inspect_canvas 的整份画布 JSON）
    truncated: bool = False


class ChatResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """调用 Pydantic AI agent，返回文本 + 工具调用轨迹。"""
    if not req.message.strip():
        raise AppError("message 不能为空", status_code=422)

    # run_chat 是 chat_agent 的出口包装：内含 LangSmith 埋点（agent.chat）+ 资源上限，
    # 并返回**已配好工具返回值**的轨迹（见 chat_agent.extract_tool_calls 的说明）
    from app.agent.chat_agent import run_chat
    from app.agent.chat_store import store as chat_history

    # 真历史优先：有 conversation_id 且 Redis 里存着结构化消息（含工具调用与返回值）时用它，
    # 本轮消息由 Pydantic AI 追加在后。拿不到（无 id / Redis 降级 / 历史过期 / 数据损坏）
    # 就回落到旧的「前端拍平文本历史」路径 —— 所以 Redis 挂了只是退化，不是坏掉。
    real_history = await chat_history.load(req.conversation_id) if req.conversation_id else None

    # 把当前画布 id 放到 prompt 上下文里，agent 可以直接引用
    context = f"当前画布项目 id: {req.canvas_id}" if req.canvas_id else "未指定画布项目 id（请先问用户）"

    if real_history:
        # ⚠️ 有真历史时**不再**把前端文本历史拼进 prompt：两套同时上会让模型看到
        #    重复内容（服务端历史 + 拍平文本），反而更容易答乱、还白烧 token。
        full_prompt = f"{context}\n用户消息：{req.message}"
    else:
        # 回落路径：构造完整用户 prompt：历史对话 + 系统上下文 + 本轮消息
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

    sink: dict = {}
    try:
        out = await run_chat(full_prompt, history=real_history, sink=sink)
    except Exception as exc:
        logger.error("Agent 调用失败: %s", exc, exc_info=True)
        # 原始异常只进日志；用户看到的是中文友好映射（与全局异常层同一规范）
        raise AppError(
            friendly_error_message(exc),
            status_code=500,
            detail=str(exc),
            retryable=True,
        )

    # 写回本轮之后的完整历史（含工具调用与返回值）。失败静默，绝不影响响应。
    if req.conversation_id and sink.get("messages"):
        await chat_history.save(req.conversation_id, sink["messages"])

    return ChatResponse(
        code=0,
        message="ok",
        data={
            "reply": out["reply"],
            "tool_calls": out.get("tool_calls") or [],
            "canvas_id": req.canvas_id,
            "model": out.get("model", "unknown"),
            # 本轮真实消耗（请求数 / 工具调用数 / tokens）：排障与成本核算都用得上
            "usage": out.get("usage"),
            "conversation_id": req.conversation_id,
            # 本轮实际用的是哪套历史 —— 排障时一眼看出真历史是否生效
            "history_source": "server" if real_history else "client",
        },
    )


@router.delete("/chat/{conversation_id}", response_model=ChatResponse)
async def clear_chat(conversation_id: str) -> ChatResponse:
    """清掉一段对话的服务端历史（前端「清空对话」时调用）。

    不清的话：用户点「清空对话」只是前端把气泡删了，服务端历史还在 ——
    下一轮 agent 仍会引用「已经不存在的对话」，是最典型的「界面说清空了、其实没清」。
    """
    from app.agent.chat_store import store as chat_history

    await chat_history.clear(conversation_id)
    return ChatResponse(code=0, message="ok",
                        data={"conversation_id": conversation_id, "cleared": True})


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
    from app.gateway.agnes import gateway

    # ★ 2026-09-23 修：此处原本是**第三套** LLM 出口 ——
    #   `httpx.AsyncClient(timeout=60).post(f"{settings.agnes_base_url}/chat/completions")`
    #   直连**国际端点单点**（国内 key 一直闲着）、无退避重试、无 LangSmith 埋点，
    #   而且 AsyncClient 建了不 close。同一个「AI 丰富提示词」功能因此在 LangSmith 里
    #   完全查不到，国际端点抖动时也没有切换与退避。
    # 现改为走统一出口 gateway.chat：provider 池 + session 粘性 + 429/503 退避 +
    #   tracing + 连接复用（与 /v1/text/generate 同一条路）。
    # system 与 user 合并成一条 user message：gateway.chat 是单消息接口，不为一个
    #   调用点扩协议（text/generate 已是同一写法，见 main.py 的 text_generate）。
    # ⚠️ 刻意不传 max_tokens：agnes 文本模型先吐 reasoning_content，额度给小会把
    #   content 吃成空串（实测，见 gateway.chat_with_images 的注释），保持默认 4096。
    try:
        enriched = (await gateway.chat(
            f"{_ENRICH_SYSTEM[req.gen_type]}\n\n{req.prompt.strip()}",
            model=settings.text_model,
            temperature=0.7,
        ) or "").strip()
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
