"""SSE 事件总线的基础测试（实时轨迹事件流）。

重连补漏（Last-Event-ID / 环形缓冲 / gap 提示）见 `test_events_replay.py`；
本文件只覆盖两条基础契约：**实时推送能唤醒消费者** 与 **无消费者时不炸**。

⚠️ `subscribe()` 的契约在 F2 之后变了：从「返回一个 Queue」改成
「返回 `(replay, bus)`」，读事件走 `bus.since(cursor)` + `bus.cond.wait()`。
"""
import asyncio

import pytest

from app import events


@pytest.mark.asyncio
async def test_live_stream_wakes_waiter():
    """消费者在等新事件时，`emit` 必须把它唤醒（实时推送的核心路径）。"""
    session = "sse-test-live"
    await events.clear(session)
    _, bus = await events.subscribe(session)
    cursor = 0

    async def wait_next():
        async with bus.cond:
            while not bus.since(cursor):
                await bus.cond.wait()
            return bus.since(cursor)[0]

    waiter = asyncio.create_task(wait_next())
    await asyncio.sleep(0.05)  # 让 waiter 先真正进入 cond.wait()
    assert not waiter.done(), "前置条件：此刻还没有事件"

    await events.emit(session, "node_entered", {"node_id": "requirement_parser"})
    event = await asyncio.wait_for(waiter, timeout=1.0)

    assert event["type"] == "node_entered"
    assert event["data"]["node_id"] == "requirement_parser"
    assert event["session_id"] == session

    await events.unsubscribe(session)
    await events.clear(session)


@pytest.mark.asyncio
async def test_emit_without_consumer_is_dropped():
    """无消费者时 emit 不抛错（节点性能不被事件系统拖累）。"""
    await events.emit("sse-no-consumer", "node_entered", {})


@pytest.mark.asyncio
async def test_sse_format():
    event = {"event_id": 1, "session_id": "x", "type": "progress",
             "timestamp": 0, "data": {"progress": 50}}
    text = events.sse_format(event)
    assert "event: progress" in text
    assert '"progress": 50' in text
    assert text.endswith("\n\n")
    # 原生 id 行：浏览器 EventSource 靠它自动回填 Last-Event-ID
    assert text.startswith("id: 1\n")
