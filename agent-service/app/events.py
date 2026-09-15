"""SSE 事件总线：会话级事件收发 **+ 断线重连补漏**。

## 为什么需要缓冲（F2）

原实现是「每个 session 一个 `asyncio.Queue`」，有三个缺陷：

1. **无订阅者 = 事件直接丢弃**（`emit` 里 `if queue is None: return`）。
   于是「节点开始跑」到「前端 EventSource 连上」之间的**全部事件永久丢失** ——
   而任务提交到前端建立 SSE 之间必然有时间差，所以轨迹开头总是缺的。
2. `unsubscribe()` 直接 `_buses.pop()` → **刷新页面立刻丢缓冲**，重连拿不到任何东西。
3. `event_id = int(time.time() * 1000)` → 同一毫秒内多条**撞号**，且墙钟**非严格递增**
   （NTP 回拨会倒退）→ 基于 `Last-Event-ID` 的续传根本无法工作。

现改为：每会话一个环形缓冲 `deque(maxlen=500)` + 单调递增计数器 + `Condition` 通知。

## 缓冲生命周期（刻意定义清楚）

- **只在首次 `subscribe()` 时创建** —— 保留原来「没人看就不缓冲」的内存语义
- **`unsubscribe()` 不销毁缓冲**，只减消费者计数 —— 这是能重连补漏的**前提**
- 缓冲由 `deque(maxlen=500)` 自然封顶；总线本体在会话终态由 `clear()` 释放
- 重连时若 `last_event_id` 早于缓冲最旧一条 → 发一条 `replay_gap` 告知客户端
  **有事件已过期**，而不是静默地让轨迹缺一段
"""
import asyncio
import json
import time
from collections import deque
from typing import Any

# 每会话最多缓冲 500 条（远超单次任务的事件量，又能自然封顶内存）
_BUF_MAX = 500

# 心跳间隔：超过这个时间没有新事件就发一行注释，防止代理超时断连
HEARTBEAT_SECONDS = 15.0


class Bus:
    """单个会话的事件总线。"""

    __slots__ = ("buf", "cond", "next_id", "consumers")

    def __init__(self) -> None:
        self.buf: deque[dict] = deque(maxlen=_BUF_MAX)
        self.cond = asyncio.Condition()
        self.next_id = 1
        self.consumers = 0

    def since(self, last_event_id: int | None) -> list[dict]:
        """取 `last_event_id` 之后的事件（None = 全量）。"""
        if last_event_id is None:
            return list(self.buf)
        return [e for e in self.buf if e["event_id"] > last_event_id]

    def lost_before(self, last_event_id: int | None) -> int | None:
        """客户端是否漏掉了已被 ring 淘汰的事件？返回最旧可用 id（没漏则 None）。

        ⚠️ 只有「**确实看过一些事件**」的客户端才可能漏：
        `last_event_id` 为 None / 0 / 负数表示它从没收到过任何事件，
        此时缓冲里现存的全都是「新的」，不是「漏的」—— 报 gap 会误导前端。
        """
        if last_event_id is None or last_event_id < 1 or not self.buf:
            return None
        oldest = self.buf[0]["event_id"]
        # last_event_id + 1 就是客户端期望的下一条；拿不到说明中间被淘汰了
        if last_event_id + 1 < oldest:
            return oldest
        return None


_buses: dict[str, Bus] = {}
_lock = asyncio.Lock()


async def emit(session_id: str, etype: str, data: dict[str, Any] | None = None) -> None:
    """发射一条轨迹事件。

    **没有总线（从未有人订阅）时静默丢弃** —— 保留「没人看就不占内存」的语义。
    一旦有人订阅过，缓冲会一直留到会话终态，重连可补。
    """
    bus = _buses.get(session_id)
    if bus is None:
        return
    async with bus.cond:
        event = {
            # 顺序用**每会话单调递增计数器**（原先是墙钟毫秒：同毫秒撞号 + 回拨会倒退，
            # 导致 Last-Event-ID 续传不可靠）
            "event_id": bus.next_id,
            "session_id": session_id,
            "type": etype,
            # timestamp 仍是墙钟：前端拿它显示「什么时候发生的」，
            # 顺序与去重一律看 event_id，两者职责分开
            "timestamp": int(time.time()),
            "data": data or {},
        }
        bus.next_id += 1
        bus.buf.append(event)
        bus.cond.notify_all()


async def subscribe(session_id: str, last_event_id: int | None = None) -> tuple[list[dict], Bus]:
    """订阅会话事件流。

    Returns:
        `(replay, bus)`：replay 是 `last_event_id` 之后可补的历史事件（首次订阅时为全量），
        bus 供调用方等待后续新事件。

    重复订阅**返回同一个 bus**（多个消费者共享缓冲，各自维护游标）。
    """
    async with _lock:
        bus = _buses.get(session_id)
        if bus is None:
            bus = Bus()
            _buses[session_id] = bus
        bus.consumers += 1

    replay = bus.since(last_event_id)
    lost = bus.lost_before(last_event_id)
    if lost is not None:
        # 明确告知「你漏掉了一段」，而不是让前端以为轨迹本来就缺
        replay.insert(0, {
            "event_id": max(0, lost - 1),
            "session_id": session_id,
            "type": "replay_gap",
            "timestamp": 0,
            "data": {"oldest_available": lost},
        })
    return replay, bus


async def unsubscribe(session_id: str) -> None:
    """消费者断开。

    ⚠️ **刻意不销毁缓冲**：刷新页面/网络抖动后重连要靠它补漏。
    缓冲的释放交给会话终态的 `clear()`。
    """
    bus = _buses.get(session_id)
    if bus is None:
        return
    async with _lock:
        bus.consumers = max(0, bus.consumers - 1)


async def clear(session_id: str) -> None:
    """会话终态时释放总线（由 `_run_session` 的 finally 调用）。"""
    async with _lock:
        _buses.pop(session_id, None)


def bus_count() -> int:
    """当前存活的总线数（测试/观测用）。"""
    return len(_buses)


def sse_format(event: dict) -> str:
    """格式化为 SSE 报文。

    带上原生 `id:` 字段 —— 浏览器 EventSource 断线重连时会自动把它放进
    `Last-Event-ID` 请求头，这正是补漏能工作的前提。
    """
    lines = [
        f"id: {event['event_id']}",
        f"event: {event['type']}",
        f"data: {json.dumps(event, ensure_ascii=False)}",
    ]
    return "\n".join(lines) + "\n\n"
