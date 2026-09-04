"""会话调度器（任务排期队列）。

设计目标：取代「提交即 asyncio.create_task 立刻执行」的无界并发，
改为「有界并发 + FIFO 队列」：同一时刻最多跑 max_concurrent_sessions 个会话，
其余会话在队列中排队，轮到时再启动，避免多个长任务同时压向 Agnes API 触发限流。

结构（轻量 in-process 消息队列，不引入外部 MQ 依赖）：
- _queue: asyncio.Queue[str]，session_id 的 FIFO 待执行队列
- _running: set[str]，当前正在执行的会话
- worker 协程：常驻 N 个（= max_concurrent_sessions），循环从队列取任务并执行

对外契约：
- submit(session_id)  → 入队，返回排队编号（1 起）
- cancel(session_id)  → 任务尚未开始执行则移除；已执行则返回 False（不打断运行中会话）
- snapshot()          → 执行中 + 排队中的 session_id 列表（画廊排期展示用）
"""
import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class SessionScheduler:
    """基于 asyncio.Queue 的有界并发会话调度器。"""

    def __init__(
        self,
        max_concurrent: int = 2,
        maxsize: int = 200,
        runner: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> None:
        self._max_concurrent = max(1, max_concurrent)
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._running: set[str] = set()
        self._workers: list[asyncio.Task] = []
        self._running_flag = False
        self._runner = runner or self._default_runner

    # ---- 生命周期 ----

    async def start(self, runner: Optional[Callable[[dict], Awaitable[None]]] = None) -> None:
        """启动固定数量的 worker。幂等：重复调用不会重复创建。"""
        if self._running_flag:
            if runner:
                self._runner = runner
            return
        if runner:
            self._runner = runner
        self._running_flag = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"session-worker-{i}")
            for i in range(self._max_concurrent)
        ]
        logger.info("SessionScheduler 启动：并发上限=%d", self._max_concurrent)

    async def stop(self) -> None:
        """停止所有 worker；运行中的会话任务不主动取消（让其自然结束或由调用方处理）。"""
        self._running_flag = False
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        logger.info("SessionScheduler 已停止，残留执行中会话=%d", len(self._running))

    # ---- 对外操作 ----

    def submit(self, session_id: str) -> int:
        """入队，返回即将执行编号（排队位置+当前执行数，1 起）。"""
        self._queue.put_nowait(session_id)
        return self._queue.qsize() + len(self._running)

    def cancel(self, session_id: str) -> bool:
        """取消排队中的会话（尚未开始执行）。已在执行的返回 False。"""
        if session_id in self._running:
            return False
        try:
            self._queue._queue.remove(session_id)  # noqa: SLF001 asyncio.Queue 底层 deque
            return True
        except (ValueError, AttributeError):
            return False

    def snapshot(self) -> dict:
        """调度器当前状态快照（画廊排期展示）。"""
        queued = list(self._queue._queue)  # noqa: SLF001
        return {
            "running": list(self._running),
            "queued": queued,
            "running_count": len(self._running),
            "queued_count": len(queued),
        }

    # ---- 内部 ----

    async def _default_runner(self, state: dict) -> None:
        """兜底 runner：仅打日志。正常由 main.py 注入真实 _run_session。"""
        logger.warning(
            "SessionScheduler 未注入 runner，会话 %s 被跳过",
            state.get("session_id"),
        )

    async def _worker(self, index: int) -> None:
        """单个 worker 循环：取任务 → 标记运行 → 执行 → 清理。"""
        while self._running_flag:
            session_id: str = await self._queue.get()
            self._running.add(session_id)
            try:
                await self._run_one(session_id)
            finally:
                self._running.discard(session_id)
                self._queue.task_done()
        # 退出时把队列里残留任务丢弃，避免 worker 泄漏
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def _run_one(self, session_id: str) -> None:
        from app.main import _sessions  # 延迟导入避免循环依赖

        state = _sessions.get(session_id)
        if state is None:
            logger.warning("调度器取到未知 session: %s", session_id)
            return
        logger.info("调度器开始执行会话: %s", session_id)
        try:
            await self._runner(state)
        except Exception as exc:  # 兜底：不让 worker 崩溃
                    from app.errors import friendly_error_message
                    logger.error("调度器执行会话 %s 异常", session_id, exc_info=exc)
                    state["error_message"] = friendly_error_message(exc)


# 全局单例（与 poller 同风格）；并发上限/队列容量从配置读取
from app.config import settings as _settings

scheduler = SessionScheduler(
    max_concurrent=_settings.max_concurrent_sessions,
    maxsize=_settings.session_queue_maxsize,
)