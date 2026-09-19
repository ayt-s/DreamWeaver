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
import collections
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class QueueFullError(RuntimeError):
    """调度队列已满（**真实容量口径**）。

    ★ 2026-09-19 新增（#5）：`asyncio.Queue.put_nowait` 满了会抛 `asyncio.QueueFull`，
    它一路冒到 FastAPI 就是 500「服务器开小差了」—— 而这是**可重试的临时过载**，
    语义上必须是 429 + retryable。调用方（main.create_video_task）捕获本异常转 AppError。

    为什么不复用 asyncio.QueueFull：那是标准库的编程错误类，捕获它会让调用方
    分不清「队列满」与「queue 用错」；包一层也让错误文案能带上两个口径的数字。
    """


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
        # 排期镜像：_pending 与 _queue 中的 session_id 一一对应（FIFO 同序），
        # 供 subscribe/cancel/snapshot 用公开状态判断，避免访问 asyncio.Queue 私有 deque。
        self._pending: collections.deque[str] = collections.deque()
        # 软取消标记：排队期间被 cancel 的 session，worker 取到时跳过执行。
        self._cancelled: set[str] = set()
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
        """停止所有 worker；运行中的会话任务不主动取消（让其自然结束或由调用方处理）。

        ★ 2026-09-19 修（#1 的姊妹项）：停机时**不再清空待执行队列**。
        改前形状：`_worker` 退出时 `self._pending.clear()` + 把 `asyncio.Queue` 里
        残留的任务全部 get_nowait 丢掉。于是 Ctrl+C 时排在队里的会话既不会执行，
        也不会有任何回调发出 —— Java 侧任务停在 queued，看门狗转 interrupted，
        靠启动恢复兜底。而恢复靠的是 `dw:agent:active` 索引（提交时就写好了），
        所以**丢掉队列不会丢会话**，但会让「本进程还剩多少待执行」这一状态在停机
        瞬间不可观测（日志里 `残留执行中会话=0` 却其实还有十几个在排队）。
        现在保留 `_pending` 镜像与队列内容，只清 worker 引用 —— 进程退出即随内存消失，
        不泄漏；日志能如实报出「仍待执行 N 个」。
        """
        self._running_flag = False
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        logger.info(
            "SessionScheduler 已停止，残留执行中会话=%d，仍待执行（交由重启恢复）=%d",
            len(self._running), len(self._pending),
        )

    # ---- 对外操作 ----

    def submit(self, session_id: str) -> int:
        """入队，返回即将执行编号（排队位置+当前执行数，1 起）。

        队列满时抛 `QueueFullError`（见类注释）。**不要**在这里吞掉——调用方
        需要把「没入队」与「已入队」区分开，否则会留下永远不执行的僵尸会话。

        ★ 2026-09-19 修（#5）：原来直接 `self._queue.put_nowait(...)`，满时抛的是
        `asyncio.QueueFull` 一路冒到 HTTP 层变成 500；现在包成 `QueueFullError`，
        由 main 转 429 + retryable，并回滚已写入的会话态。
        """
        try:
            self._queue.put_nowait(session_id)
        except asyncio.QueueFull as exc:
            raise QueueFullError(
                f"调度队列已满（真实占用 {self.queue_depth()}/{self.queue_maxsize()}）"
            ) from exc
        self._pending.append(session_id)
        # 只数「仍会执行」的排队项：已标记取消、worker 尚未取走的项不计入
        pending_live = sum(1 for s in self._pending if s not in self._cancelled)
        return pending_live + len(self._running)

    # ---- 容量口径（#5 的关键：两个数字必须来自同一个来源）----
    #
    # ⚠️ 2026-09-19 修（#5）：`session_queue_maxsize` 这个上限本来是**两套口径**：
    #   - 真正的限流闸门是 `asyncio.Queue(maxsize=maxsize)`（scheduler.py:35），
    #     它数的入队对象是**队列里剩下的一切**，含「排队期被软取消、但 worker
    #     还没取走」的残留项，以及「worker 已取走但尚未 task_done」的项；
    #   - 而 main.create_video_task 的预检读的是 `snapshot()["queued_count"]`
    #     （scheduler.py:100-108），它按 `_pending` 镜像数**且排除 `_cancelled`**。
    #   于是镜像可以比真实占用小 → 预检说「还有位置」，`put_nowait` 却抛 QueueFull。
    #   取消越多、偏差越大（2026-09 实测路径：用户连续取消排队任务后提交 → 500）。
    #   下面两个方法把预检与闸门统一到 `asyncio.Queue` 的真实占用上。

    def queue_depth(self) -> int:
        """队列真实占用（含软取消残留、含 worker 未 task_done 的项）。"""
        return self._queue.qsize()

    def queue_maxsize(self) -> int:
        """队列真实容量（与 asyncio.Queue 的 maxsize 同源）。"""
        return self._queue.maxsize

    def is_queue_full(self) -> bool:
        """真实口径的「满了」。预检必须用这个，不能用 snapshot()["queued_count"]。"""
        return self.queue_depth() >= self.queue_maxsize()

    def cancel(self, session_id: str) -> bool:
        """取消排队中的会话（尚未开始执行）。已在执行的返回 False。

        软取消：只在调度器自己的状态里打标记，不触碰 asyncio.Queue 内部结构；
        worker 取到被标记的 session 时直接跳过（绝不执行 runner）。
        """
        if session_id in self._running:
            return False
        if session_id in self._pending:
            self._cancelled.add(session_id)
            self._pending.remove(session_id)
            return True
        return False

    def snapshot(self) -> dict:
        """调度器当前状态快照（画廊排期展示）。"""
        queued = [s for s in self._pending if s not in self._cancelled]
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
            # 出队即从排期镜像移除（无论后续执行还是跳过）
            if session_id in self._pending:
                self._pending.remove(session_id)
            if session_id in self._cancelled:
                # 排队期间已被 cancel：回收标记与队列计数，绝不执行
                self._cancelled.discard(session_id)
                self._queue.task_done()
                continue
            self._running.add(session_id)
            try:
                await self._run_one(session_id)
            finally:
                self._running.discard(session_id)
                self._queue.task_done()
        # ★ 2026-09-19 修（#1 的姊妹项）：**不再丢弃**队列里残留的任务。
        #   改前形状：`self._pending.clear()` + 循环 get_nowait 把待执行会话全扔掉。
        #   丢掉的会话本进程不会再执行，也不会有任何回调 → Java 侧停在 queued 等看门狗。
        #   这些会话在 `dw:agent:active` 里仍有索引（提交时就写了），重启恢复能捡回来，
        #   所以不是丢数据；但停机日志会谎报「没有待执行」。现在原样保留，只由进程退出清理。

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