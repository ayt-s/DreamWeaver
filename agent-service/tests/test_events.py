"""SSE 事件总线的测试（新功能：实时轨迹事件流）。"""
import asyncio
import pytest

from app import events


@pytest.mark.asyncio
async def test_emit_and_receive():
    session = "sse-test-1"
    queue = await events.subscribe(session)
    await events.emit(session, "node_entered", {"node_id": "requirement_parser"})
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event["type"] == "node_entered"
    assert event["data"]["node_id"] == "requirement_parser"
    await events.unsubscribe(session)


@pytest.mark.asyncio
async def test_emit_without_consumer_is_dropped():
    """无消费者时 emit 不抛错（节点性能不被拖累）。"""
    await events.emit("sse-no-consumer", "node_entered", {})


@pytest.mark.asyncio
async def test_sse_format():
    event = {"event_id": 1, "session_id": "x", "type": "progress",
             "timestamp": 0, "data": {"progress": 50}}
    text = events.sse_format(event)
    assert "event: progress" in text
    assert '"progress": 50' in text
    assert text.endswith("\n\n")