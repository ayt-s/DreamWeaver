"""SSE 事件总线：断线重连补漏（F2）。

改造前的三个缺陷，逐条用测试钉死：

1. `emit` 在无人订阅时丢弃事件 —— 而「任务提交」到「前端 EventSource 连上」
   之间必然有时间差，所以轨迹开头总是缺的
2. `unsubscribe` 直接 pop 整个 bus —— 刷新页面立刻丢缓冲，重连拿不到任何东西
3. `event_id = int(time.time() * 1000)` —— 同一毫秒多条**撞号**，且墙钟非严格递增
"""
import asyncio

import pytest

from app import events


async def _fresh(sid: str):
    """订阅一个干净的会话，返回 bus。"""
    await events.clear(sid)
    _, bus = await events.subscribe(sid)
    return bus


# ------------------------------------------------------ 核心：重连补漏

@pytest.mark.asyncio
async def test_reconnect_replays_only_missed_events():
    """发 5 条、断线、带 last_event_id=2 重连 → 只补 3/4/5。"""
    sid = "t-replay"
    await _fresh(sid)

    for i in range(1, 6):
        await events.emit(sid, "progress", {"i": i})

    # 断开（模拟刷新页面）
    await events.unsubscribe(sid)
    assert events.bus_count() > 0, "unsubscribe 不该销毁总线 —— 否则补漏永远拿不到东西"

    replay, _ = await events.subscribe(sid, last_event_id=2)
    assert [e["event_id"] for e in replay] == [3, 4, 5]
    assert [e["data"]["i"] for e in replay] == [3, 4, 5]

    await events.clear(sid)


@pytest.mark.asyncio
async def test_first_subscribe_replays_nothing():
    """首次订阅时缓冲是空的 —— 保留「没人看就不缓冲」的语义。"""
    sid = "t-first"
    await events.clear(sid)

    replay, _ = await events.subscribe(sid)
    assert replay == []
    await events.clear(sid)


@pytest.mark.asyncio
async def test_no_subscriber_means_event_is_dropped():
    """从未订阅过 → emit 静默丢弃，不建总线（内存行为与改造前一致）。"""
    sid = "t-nobody"
    await events.clear(sid)
    before = events.bus_count()

    await events.emit(sid, "progress", {"i": 1})
    await events.emit(sid, "progress", {"i": 2})

    assert events.bus_count() == before, "没人订阅时不该创建总线"

    # 之后订阅：拿不到「丢弃期」的事件（这是刻意的，与改造前语义一致）
    replay, _ = await events.subscribe(sid)
    assert replay == []
    await events.clear(sid)


# ------------------------------------------------------ event_id 单调性

@pytest.mark.asyncio
async def test_event_ids_are_monotonic_without_collision():
    """同一毫秒内连发多条也必须严格递增、互不撞号。

    回归：原先 event_id = int(time.time() * 1000)，循环里连发会得到
    **一串相同值** → 前端按 id 去重/续传全部失效。
    """
    sid = "t-monotonic"
    await _fresh(sid)

    for _ in range(20):
        await events.emit(sid, "progress", {})

    ids = [e["event_id"] for e in list(_bus(sid).buf)]
    assert ids == sorted(set(ids)), f"id 必须严格递增且唯一，实际 {ids}"
    assert ids == list(range(1, 21)), f"应为 1..20，实际 {ids}"

    await events.clear(sid)


def _bus(sid: str):
    return events._buses[sid]


# ------------------------------------------------------ 缓冲生命周期

@pytest.mark.asyncio
async def test_clear_releases_bus():
    """会话终态 clear 后总线被释放，不会无界增长。"""
    sid = "t-clear"
    await _fresh(sid)
    await events.emit(sid, "progress", {})

    await events.clear(sid)
    assert sid not in events._buses

    # 释放后再订阅：缓冲已没了，拿不到旧事件
    replay, _ = await events.subscribe(sid)
    assert replay == []
    await events.clear(sid)


@pytest.mark.asyncio
async def test_ring_buffer_is_bounded():
    """缓冲由 deque(maxlen) 自然封顶 —— 发再多也不会无限占内存。"""
    sid = "t-bound"
    await _fresh(sid)

    for i in range(events._BUF_MAX + 50):
        await events.emit(sid, "progress", {"i": i})

    bus = _bus(sid)
    assert len(bus.buf) == events._BUF_MAX
    # 保留的是**最新**的一批
    assert bus.buf[-1]["data"]["i"] == events._BUF_MAX + 49

    await events.clear(sid)


@pytest.mark.asyncio
async def test_overflow_gap_is_reported_not_silent():
    """客户端要的事件已被 ring 淘汰时，必须明确告知「你漏了一段」。

    否则前端会以为轨迹本来就缺一段 —— 静默丢数据是最难排查的那类问题。
    """
    sid = "t-gap"
    await _fresh(sid)

    for i in range(events._BUF_MAX + 10):
        await events.emit(sid, "progress", {"i": i})
    oldest = _bus(sid).buf[0]["event_id"]

    # 客户端只看到过第 1 条，其余全被 ring 淘汰了
    replay, _ = await events.subscribe(sid, last_event_id=1)
    assert replay[0]["type"] == "replay_gap", f"应给 gap 提示，实际 {replay[0]}"
    assert replay[0]["data"]["oldest_available"] == oldest
    assert replay[1]["event_id"] == oldest

    await events.clear(sid)


# ------------------------------------------------------ SSE 报文格式

@pytest.mark.asyncio
async def test_sse_format_carries_id_line():
    """必须带原生 `id:` 行 —— 浏览器 EventSource 靠它自动回填 Last-Event-ID。

    少了这一行，重连补漏在真实浏览器里根本不会触发（服务端逻辑再对也没用）。
    """
    sid = "t-format"
    await _fresh(sid)
    await events.emit(sid, "node_entered", {"node_id": "qc_checker"})

    raw = events.sse_format(_bus(sid).buf[0])
    assert raw.startswith("id: 1\n"), f"缺少 id 行: {raw!r}"
    assert "event: node_entered" in raw
    assert "data: {" in raw
    assert raw.endswith("\n\n"), "SSE 报文必须以空行结尾"
    # 中文不能转义成 \uXXXX（前端直接显示）
    await events.clear(sid)


def test_sse_format_keeps_unicode_readable():
    raw = events.sse_format({
        "event_id": 7, "type": "progress", "session_id": "s",
        "timestamp": 0, "data": {"phase": "复用第 1 段"},
    })
    assert "复用第 1 段" in raw and "\\u" not in raw


# ------------------------------------------------------ main.py 的头解析

def test_parse_last_event_id():
    from app.main import _parse_last_event_id

    assert _parse_last_event_id("3") == 3
    assert _parse_last_event_id(" 12 ") == 12
    assert _parse_last_event_id(None) is None
    assert _parse_last_event_id("") is None
    assert _parse_last_event_id("abc") is None, "脏头必须当「从头开始」而不是抛异常"
    assert _parse_last_event_id("-5") == -5   # 负数也会被 since() 正确处理（等于全量）


@pytest.mark.asyncio
async def test_negative_or_absent_last_id_replays_everything():
    """负数/缺失的 last_event_id 等价于「从头补全」，且**不该误报 gap**。

    回归：`lost_before` 起初只看 `last_event_id + 1 < oldest`，于是
    `last_event_id=-1` 会被判成「客户端漏了事件」—— 但从没收到过任何事件的
    客户端不可能「漏」，报 gap 只会误导前端。
    """
    sid = "t-negative"
    await _fresh(sid)
    for i in range(3):
        await events.emit(sid, "progress", {"i": i})

    replay_neg, _ = await events.subscribe(sid, last_event_id=-1)
    replay_none, _ = await events.subscribe(sid, last_event_id=None)
    replay_zero, _ = await events.subscribe(sid, last_event_id=0)

    assert len(replay_neg) == 3 and len(replay_none) == 3 and len(replay_zero) == 3
    for replay in (replay_neg, replay_none, replay_zero):
        assert all(e["type"] != "replay_gap" for e in replay), "不该误报 gap"

    await events.clear(sid)
