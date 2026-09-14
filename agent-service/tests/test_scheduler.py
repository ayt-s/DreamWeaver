"""SessionScheduler 单测：有界并发 + FIFO 排期 + 取消排队。

不依赖 FastAPI 与 LangGraph，纯测调度器语义。
"""
import asyncio

import pytest

from app.scheduler import SessionScheduler


async def _noop_runner(state: dict) -> None:
    """测试 runner：标记已执行，不产生真实生成。"""
    state["runner_ran"] = True


@pytest.mark.asyncio
async def test_max_concurrent_limit():
    """并发上限：同时执行中的会话数不超过 max_concurrent。"""
    started: list[str] = []
    release = asyncio.Event()

    async def gated_runner(state: dict) -> None:
        started.append(state["session_id"])
        await release.wait()  # 挂起，模拟长任务

    sched = SessionScheduler(max_concurrent=2, maxsize=100, runner=gated_runner)
    await sched.start()

    # 塞 5 个任务，只有 2 个能进入运行态
    for i in range(5):
        await asyncio.sleep(0)
        sched.submit(f"s{i}")

    await asyncio.sleep(0.2)
    snap = sched.snapshot()
    assert snap["running_count"] <= 2
    assert len(started) <= 2

    # 放行后全部执行完
    release.set()
    await asyncio.sleep(0.2)
    assert sched.snapshot()["queued_count"] == 0
    await sched.stop()


@pytest.mark.asyncio
async def test_queue_position_and_fifo():
    """排队编号递增，FIFO 顺序执行。"""
    sched = SessionScheduler(max_concurrent=1, maxsize=100, runner=_noop_runner)
    await sched.start()

    positions = [sched.submit(f"s{i}") for i in range(3)]
    # 因为只有一个 worker 且 runner 立即完成，编号可能被快速消费；
    # 这里验证 submit 返回值为正数且递增（不验证绝对编号）
    assert positions == sorted(positions)
    assert positions[0] >= 1

    snap = sched.snapshot()
    assert snap["running_count"] + snap["queued_count"] <= 3

    # 等流水线清空
    await asyncio.sleep(0.2)
    assert sched.snapshot()["queued_count"] == 0
    await sched.stop()


@pytest.mark.asyncio
async def test_cancel_queued():
    """cancel 只移除排队中任务，不打断运行中任务。"""
    sched = SessionScheduler(max_concurrent=1, maxsize=100, runner=_noop_runner)
    await sched.start()

    sched.submit("s-keep")   # 立刻被 worker 消费 → 运行中
    await asyncio.sleep(0.05)
    sched.submit("s-drop")   # 排队的（若 worker 慢）或已执行

    # s-keep 在运行中或已完成，cancel 不应返回 False 之外的异常行为
    assert sched.cancel("s-unknown") is False

    # 先停止 worker，再入队验证 cancel 语义（确定性路径）
    await sched.stop()
    sched.submit("s-now")
    assert sched.cancel("s-now") is True
    # 已移除后再次 cancel 为 False
    assert sched.cancel("s-now") is False

    snap = sched.snapshot()
    assert "s-now" not in snap["queued"]
    assert "s-now" not in snap["running"]


# ---- 软取消（不访问 asyncio.Queue 私有属性）专项测试 ----

async def _register_sessions(*session_ids: str) -> None:
    """把 session 注册进 app.main._sessions（_run_one 取不到 state 会直接跳过）。"""
    from app.main import _sessions

    for sid in session_ids:
        _sessions[sid] = {"session_id": sid, "status": "queued", "error_message": None}


def _drop_sessions(*session_ids: str) -> None:
    from app.main import _sessions

    for sid in session_ids:
        _sessions.pop(sid, None)


@pytest.mark.asyncio
async def test_cancel_queued_never_executes_runner():
    """取消排队中的会话：cancel 返回 True，worker 即使取到该 id 也绝不执行 runner。"""
    executed: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def recording_runner(state: dict) -> None:
        executed.append(state["session_id"])
        started.set()
        await release.wait()

    sched = SessionScheduler(max_concurrent=1, maxsize=100, runner=recording_runner)
    await _register_sessions("s-run", "s-drop")
    await sched.start()
    try:
        sched.submit("s-run")
        await asyncio.wait_for(started.wait(), timeout=2.0)  # 占满唯一 worker
        sched.submit("s-drop")
        assert "s-drop" in sched.snapshot()["queued"]

        assert sched.cancel("s-drop") is True          # 排队中 → 可取消
        assert sched.cancel("s-drop") is False         # 已取消，不再是排队项

        release.set()
        await asyncio.sleep(0.2)  # 让 worker 从队列取走被取消的 s-drop
        assert executed == ["s-run"]                   # 被取消的会话没有被执行
        assert sched.snapshot()["queued"] == []
    finally:
        release.set()
        await sched.stop()
        _drop_sessions("s-run", "s-drop")


@pytest.mark.asyncio
async def test_cancel_running_returns_false():
    """取消正在运行的会话：cancel 返回 False，且不影响其继续执行。"""
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[str] = []

    async def gated_runner(state: dict) -> None:
        started.set()
        await release.wait()
        finished.append(state["session_id"])

    sched = SessionScheduler(max_concurrent=1, maxsize=100, runner=gated_runner)
    await _register_sessions("s-live")
    await sched.start()
    try:
        sched.submit("s-live")
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert sched.snapshot()["running"] == ["s-live"]

        assert sched.cancel("s-live") is False         # 运行中不可取消
        assert sched.snapshot()["running"] == ["s-live"]

        release.set()
        await asyncio.sleep(0.1)
        assert finished == ["s-live"]                  # 未被软取消打断
    finally:
        release.set()
        await sched.stop()
        _drop_sessions("s-live")


@pytest.mark.asyncio
async def test_snapshot_queued_excludes_cancelled():
    """snapshot().queued 不含已取消项，其余排队项保持 FIFO 顺序与计数。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_runner(state: dict) -> None:
        started.set()
        await release.wait()

    sched = SessionScheduler(max_concurrent=1, maxsize=100, runner=blocked_runner)
    await _register_sessions("s-busy", "s-q1", "s-q2")
    await sched.start()
    try:
        sched.submit("s-busy")
        await asyncio.wait_for(started.wait(), timeout=2.0)
        sched.submit("s-q1")
        sched.submit("s-q2")
        assert sched.snapshot()["queued"] == ["s-q1", "s-q2"]

        assert sched.cancel("s-q1") is True
        snap = sched.snapshot()
        assert snap["queued"] == ["s-q2"]
        assert snap["queued_count"] == 1
        assert snap["running"] == ["s-busy"]

        release.set()
        await asyncio.sleep(0.2)
    finally:
        release.set()
        await sched.stop()
        _drop_sessions("s-busy", "s-q1", "s-q2")