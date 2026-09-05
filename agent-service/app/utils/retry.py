"""网络调用重试装饰器。

用途：把易受 wifi 抖动/超时影响的网络调用包一层指数退避，
避免短时断网导致整个任务失败。

用法：
    @with_retry("下载产物", delays=(5, 15, 45, 90))
    async def _download(url, dest): ...

    # 或按网络类型选预设：
    @with_retry("LLM 分析", preset="llm")
    async def analyze(text, model): ...
"""
from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# 预设延迟（秒），末位数字代表「最后一次也失败前的最后等待」
_PRESETS: dict[str, tuple[float, ...]] = {
    "llm": (10, 30, 60),           # LLM 调用，10s/30s/60s 覆盖常见抖动
    "download": (5, 15, 45, 90),   # 大文件下载，更长的最后等待
    "light": (5, 15, 30),          # 轻量网络调用
}

# 触发重试的异常类型：网络抖动、超时、服务端错误
_RETRYABLE: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,       # 读超时/连接超时
    httpx.ConnectError,           # DNS/连接失败
    httpx.ConnectTimeout,         # 连接超时
    httpx.ReadTimeout,            # 读超时
    httpx.RemoteProtocolError,    # 对端断连
    httpx.PoolTimeout,            # 连接池耗尽
    asyncio.TimeoutError,         # 外层超时
    ConnectionResetError,         # socket 重置
    ConnectionAbortedError,       # socket 中断
    ConnectionRefusedError,       # 连接被拒（服务重启中）
    OSError,                      # 底层 socket 错误兜底（含 InterruptedError）
)


def with_retry(
    what: str,
    delays: tuple[float, ...] | None = None,
    preset: str | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """网络调用重试装饰器（异步函数）。

    参数：
        what: 调用标识，写进日志便于排查（例如"下载产物"、"LLM 分析"）
        delays: 每次重试前的等待秒数，如 (5, 15, 45) 表示最多 3 次重试
        preset: 预设名（"llm"/"download"/"light"），与 delays 二选一

    行为：
        - 第一次调用失败后，按 delays 顺序等待再重试
        - 只有 _RETRYABLE 里的异常会触发重试，其他异常直接抛出
        - 最后一次重试仍失败时抛出最后一个异常
    """
    if delays is None:
        if preset is None:
            delays = _PRESETS["llm"]  # 默认走 LLM 预设
        else:
            delays = _PRESETS.get(preset, _PRESETS["llm"])

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            # 循环 [第 0 次尝试] + [第 1..len(delays) 次重试]
            waits = [0] + list(delays)
            for i, wait in enumerate(waits):
                try:
                    return await fn(*args, **kwargs)
                except _RETRYABLE as e:
                    last_exc = e
                    attempt = i + 1
                    if i < len(waits) - 1:
                        logger.warning(
                            "[retry] %s 失败（第 %d/%d 次）：%s：%s，%ds 后重试",
                            what, attempt, len(waits), type(e).__name__, str(e)[:120], wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    logger.error(
                        "[retry] %s 重试耗尽（%d 次），抛出：%s：%s",
                        what, attempt, type(e).__name__, str(e)[:200],
                    )
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
