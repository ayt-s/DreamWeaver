"""统一异常层。

设计原则：
- 用户看到的错误消息永远是中文、可理解的；原始异常细节只进日志，绝不直接回给前端。
- API 层统一 code/message/data 信封（与 Java CommonResult 对齐），替代 FastAPI 默认 {detail:...} 结构。
- 业务错误用 AppError（携带面向用户的中文 message + 只进日志的 detail）；
  未预期异常由兜底处理器收口（500 友好文案 + 全栈日志）。

使用方式：
  from app.errors import AppError, friendly_error_message, register_exception_handlers
  register_exception_handlers(app)   # 启动时注册一次
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """业务异常：面向用户的中文 message，原始 detail 只进日志。

    status_code 对齐 HTTP 语义（400 参数错误 / 409 冲突 / 422 校验 / 429 限流）。
    retryable 供上层（Java 看门狗、自动重试器）判断该错误是否值得重试。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        detail: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable


# 已知原始异常签名 → 用户友好中文（子串匹配，大小写不敏感；命中即返回）
_RAW_ERROR_CN: list[tuple[str, str]] = [
    # 网络层（Agent 服务不可达 / 上游抖动）
    ("connection refused", "Agent 服务暂不可用，请稍后重试"),
    ("connection reset", "Agent 服务连接被重置，请稍后重试"),
    ("connecterror", "Agent 服务暂不可用，请稍后重试"),
    ("connection attempts failed", "Agent 服务暂不可用，请稍后重试"),
    ("name or service not known", "Agent 服务地址解析失败，请联系管理员"),
    ("network is unreachable", "网络不可达，请检查网络后重试"),
    # 超时
    ("timed out", "生成服务响应超时，请稍后重试"),
    ("timeout", "生成服务响应超时，请稍后重试"),
    # 鉴权 / 资源
    ("unauthorized", "API 密钥无效或已过期，请联系管理员"),
    ("insufficient balance", "账户余额不足，请充值后重试"),
    ("insufficient", "账户余额不足，请充值后重试"),
    ("rate limit", "请求过于频繁，请稍后重试"),
    ("queue is full", "平台队列繁忙，请稍后重试"),
    ("queue full", "平台队列繁忙，请稍后重试"),
    ("503", "上游服务繁忙，请稍后重试"),
    ("502", "上游服务暂时不可用，请稍后重试"),
]


def friendly_error_message(exc: BaseException) -> str:
    """把裸异常翻译成用户可读的中文消息。

    - AppError 直接返回其 message（已经友好）；
    - 已知签名（连接拒绝/超时/余额不足/限流…）命中映射表；
    - 已有本地化文案（agnes _describe_rejection 产出，含「接口拒绝」等关键词）原样保留；
    - 其余退回 str(exc)，保证非空。
    """
    if isinstance(exc, AppError):
        return exc.message

    raw = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    # 类型名 + 消息文本一起匹配（如 httpx.ConnectError 的 str 只有 "All connection attempts failed"，类型名才是关键线索）
    lower = (type(exc).__name__ + " " + raw).lower()

    # 已中文化/已带业务语境的文案不再二次翻译
    for marker in ("接口拒绝", "公网 URL", "生成失败", "拼接失败"):
        if marker in raw:
            return raw

    for signature, friendly in _RAW_ERROR_CN:
        if signature in lower:
            return friendly

    return raw


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器（启动时调用一次）。

    顺序注意：FastAPI 按异常 MRO 匹配，越具体的越先命中；
    兜底 Exception 只接住未被任何子类处理器捕获的异常。
    """

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        if exc.detail:
            logger.warning(
                "业务异常[%d] %s (detail: %s)", exc.status_code, exc.message, exc.detail
            )
        else:
            logger.warning("业务异常[%d] %s", exc.status_code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code,
                "message": exc.message,
                "data": {
                    "retryable": exc.retryable,
                } if exc.retryable else None,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # HTTPException 的 detail 约定为中文业务文案（本服务内手动抛出），
        # 原样包进统一信封；非字符串 detail（如校验数组）统一成通用文案
        message = exc.detail if isinstance(exc.detail, str) else "请求不被接受"
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": message, "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning("请求参数校验失败: %s", jsonable_encoder(exc.errors()))
        return JSONResponse(
            status_code=422,
            content={"code": 422, "message": "请求参数不合法，请检查后重试", "data": None},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        # 兜底：完整异常进日志，用户只看到友好文案
        logger.error("未处理异常", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "服务器开小差了，请稍后重试", "data": None},
        )