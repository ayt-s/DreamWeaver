"""外部命令执行封装：兼容「不支持 asyncio 子进程」的事件循环。

背景（实测 2026-09-14，真实故障）：
Windows 上以 `--reload` 启动 uvicorn 时，其 loop factory 会以
`use_subprocess=True` 调用，事件循环退化为 **SelectorEventLoop**；而 Windows 的
SelectorEventLoop 不支持 `asyncio.create_subprocess_exec`（直接抛
`NotImplementedError`，且 **异常消息为空字符串**）。

后果：synthesizer 的时长探测 / xfade 拼接、image_slideshow 的图片转片段
全部失败；异常又被节点的兜底分支吞掉，用户看到的是「任务 completed 但没有成片、
也没有任何错误提示」——排查成本极高。

因此这里的约定：**统一用 `asyncio.to_thread` + `subprocess.run` 执行外部命令**。
- 不依赖平台特定的子进程支持，任何事件循环下都能工作（含 `--reload`）
- ffmpeg 属于 CPU/IO 密集型，放线程里跑还能避免阻塞事件循环
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    """外部命令执行结果（stdout/stderr 已解码为文本）。"""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _decode(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def _run_blocking(cmd: list[str], timeout: float) -> CommandResult:
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("命令超时（%.0fs）: %s", timeout, " ".join(cmd[:3]))
        return CommandResult(-1, _decode(exc.stdout), _decode(exc.stderr), True)
    except OSError as exc:  # 可执行文件不存在/无权限
        logger.error("命令无法执行: %s (%s)", " ".join(cmd[:3]), exc)
        return CommandResult(-1, "", str(exc))
    return CommandResult(proc.returncode, _decode(proc.stdout), _decode(proc.stderr))


async def run_command(cmd: Sequence[str], *, timeout: float) -> CommandResult:
    """执行外部命令并等待结束。

    超时不会抛异常，而是返回 `timed_out=True` 且 returncode=-1 的结果，
    让调用方按原有「失败即降级」的分支处理。
    """
    return await asyncio.to_thread(_run_blocking, [str(c) for c in cmd], timeout)
