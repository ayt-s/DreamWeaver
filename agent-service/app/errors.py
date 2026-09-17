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
#
# ⚠️ 措辞红线：**本模块跑在 agent 进程内**，所以这里出现的连接失败几乎只可能是
#    agent **自己出网**（调 agnes 生成平台 / 回调 Java），而不是「Agent 服务不可达」。
#    2026-09-17 实测踩到：任务 53 因 agnes 连接失败而失败（agent 日志里
#    `httpx.ConnectError` during `requirement_parser`），但卡片上显示的是
#    「Agent 服务暂不可用，请稍后重试」→ **用户会去重启 agent，而 agent 活得好好的**。
#    所以这里一律把失败指向**真正的下一跳**（平台/网络），不要写「Agent 服务」。
#    （Java 侧 `TaskServiceImpl` 的「Agent 服务暂不可用」是对的 —— 那是 Java 连不上
#      agent 的场景，两处语义不同，别互抄。）
_RAW_ERROR_CN: list[tuple[str, str]] = [
    # 网络层（agent 出网失败：网络 / 代理 / DNS）
    ("connection refused", "连接生成平台失败（网络或代理不通），请检查网络后重试"),
    ("connection reset", "连接生成平台被重置（网络不稳），请检查网络后重试"),
    ("connecterror", "连接生成平台失败（网络或代理不通），请检查网络后重试"),
    ("connection attempts failed", "连接生成平台失败（网络或代理不通），请检查网络后重试"),
    ("name or service not known", "域名解析失败（网络或 DNS 异常），请检查网络后重试"),
    ("network is unreachable", "网络不可达，请检查网络后重试"),
    # 超时。agnes 免费额度只有 RPM 限制，排队时响应就会变慢 → 文案要提示这一点，
    # 否则用户会以为是平台挂了（后端 gateway 侧已有同样的诊断口径，见 f84232c）
    ("timed out", "生成平台响应超时（平台排队/限流时常见），请稍后重试"),
    ("timeout", "生成平台响应超时（平台排队/限流时常见），请稍后重试"),
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