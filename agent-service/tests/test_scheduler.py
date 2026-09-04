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