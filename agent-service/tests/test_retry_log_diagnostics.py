"""重试日志必须说出**原因**（2026-09-17 实测踩到空原因）。

真实日志（任务 53 第一次尝试失败时）：

    2026-09-17 16:49:48 ERROR [app.gateway.agnes] 文本(intl) 请求异常: ，5s 后重试 (1/3)

冒号后面是空的 —— 这行日志声称说了原因，实际什么都没说，而它正是排查
「为什么这次生成失败」时要看的那一行（当时我确实对着它查了一轮网络/代理）。

根因：`_with_retry` 的重试分支直接打印 `{e}`，而 **httpx 传输层异常的 `str()`
常常是空串**（`ConnectError("All connection attempts failed")` 反而有文本，
平台排队导致的超时往往没有）。同类问题视频路径已在 f84232c 用
`_describe_transport_error` 修过，这条重试日志当时漏了。
"""

import logging

import httpx
import pytest

from app.gateway import agnes


@pytest.mark.asyncio
async def test_retry_log_names_exception_when_str_is_empty(caplog, monkeypatch):
    """★ 回归护栏：连着异常类型一起打印，不允许出现空原因。"""

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(agnes.asyncio, "sleep", _no_sleep)

    async def operation():
        raise httpx.ConnectError("")  # str(e) == ""，实测形态

    with caplog.at_level(logging.ERROR), pytest.raises(httpx.ConnectError):
        await agnes._with_retry(operation, "文本(intl)")

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert joined, "重试应当留下日志"
    assert "ConnectError" in joined, f"日志没说出异常类型名: {joined}"
    assert "异常: ，" not in joined, f"又出现了空的失败原因: {joined}"
    assert "文本(intl)" in joined, "日志丢了「哪一步失败」的上下文"


@pytest.mark.asyncio
async def test_retry_log_keeps_text_when_exception_has_one(caplog, monkeypatch):
    """异常有文本时不能被吞掉（否则改完只剩类型名，信息反而变少）。"""

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(agnes.asyncio, "sleep", _no_sleep)

    async def operation():
        raise httpx.ReadTimeout("timed out while reading response")

    with caplog.at_level(logging.ERROR), pytest.raises(httpx.ReadTimeout):
        await agnes._with_retry(operation, "视频提交[intl]")

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "ReadTimeout" in joined
    assert "timed out while reading response" in joined, f"异常原文被吞了: {joined}"
