"""SSE 事件总线：会话级事件收发，供 LangGraph 节点实时发射轨迹事件。

设计：
- 每个 session_id 一个 asyncio.Queue（有界，防消费者不消费时无限堆积）
- 节点通过 emit() 发事件，SSE 端点通过 subscribe() 订阅
- 无消费者时事件丢弃（不阻塞节点执行），保证节点性能不被事件系统拖累
"""
import asyncio
import json
import time
from typing import Any

# session_id -> asyncio.Queue[dict]
_buses: dict[str, asyncio.Queue[dict]] = {}
_lock = asyncio.Lock()
_MAX_QUEUE = 200  # 每会话最多缓冲 200 条，防止不消费时内存膨胀


async def emit(session_id: str, etype: str, data: dict[str, Any] | None = None) -> None:
    """发射一条轨迹事件。无消费者时静默丢弃。"""
    async with _lock:
        queue = _buses.get(session_id)
    if queue is None:
        return
    event = {
        "event_id": int(time.time() * 1000),  # 简易递增（毫秒级近似够用）
        "session_id": session_id,
        "type": etype,
        "timestamp": int(time.time()),
        "data": data or {},
    }
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # 消费者落后：丢弃最旧一条，避免无限积压
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def subscribe(session_id: str) -> asyncio.Queue[dict]:
    """订阅会话事件流。重复订阅返回同一队列（恰好一个消费者）。"""
    async with _lock:
        queue = _buses.get(session_id)
        if queue is None:
            queue = asyncio.Queue(maxsize=_MAX_QUEUE)
            _buses[session_id] = queue
        return queue


async def unsubscribe(session_id: str) -> None:
    """会话结束/消费者断开时清理。"""
    async with _lock:
        _buses.pop(session_id, None)


def sse_format(event: dict) -> str:
    """格式化为 SSE 报文（event: 类型 + data: JSON）。"""
    lines = [
        f"event: {event['type']}",
        f"data: {json.dumps(event, ensure_ascii=False)}",
    ]
    return "\n".join(lines) + "\n\n"