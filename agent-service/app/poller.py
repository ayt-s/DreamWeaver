"""独立视频轮询器（Phase 2 重构）。

替换原 tools/video.py 中的内联轮询，由后台 asyncio task 统一管理所有
视频任务的进度查询与完成回调。

设计原则：
- Poller 只负责轮询 + SSE 事件，不直接调 Java 回调
- Java 回调统一由 nodes/video.py 在会话级处理
"""
import asyncio
import logging
import time

from app.config import settings
from app.gateway.agnes import gateway

logger = logging.getLogger(__name__)


class VideoPoller:
    """独立轮询器，维护所有待完成视频任务的状态。"""

    def __init__(self) -> None:
        # video_id -> {model_name, session_id, shot_index, future, submitted_at, last_progress}
        self.pending_tasks: dict[str, dict] = {}
        self._poll_task: asyncio.Task | None = None
        self._running = False
        # ★ 2026-09-19 新增（#1）：优雅停机标志。
        #
        # 改前形状（「优雅停机比 kill -9 更差」的完整链条）：
        #   1. `stop()` 对每个在途 future `set_exception(TimeoutError("服务关闭超时"))`；
        #   2. `nodes/video.py` 的 `asyncio.gather(..., return_exceptions=True)` 把该异常
        #      收进 error_msgs → `video_urls` 缺这一段（甚至全空）→ 走到 `notify_final`；
        #   3. `notify_final` 的判定是「`video_urls` 为空 → status=failed」，随即回调
        #      Java（nodes/notify_final.py:73-75 附近）；
        #   4. Java `NotifyServiceImpl` 把任务落成**终态 failed**（终态后所有迟到回调被丢）；
        #   5. 进程重启 → `recovery.recover_session` 先 `probe_session_tracked(sid)`，
        #      拿到 tracked=false（任务已终态）→ **直接跳过该会话**（recovery.py:219-223 附近），
        #      设计里的「重启续跑」彻底丢失，已付费的 agnes 产物也再没人取回。
        #   而 kill -9 不执行 `stop()`：在途 future 随进程消失，**没有任何**错误回调发出，
        #   Java 侧任务停在非终态 → 看门狗转 interrupted → 重启后恢复流程照常接管。
        #   也就是说「优雅」反而把可恢复的会话变成了不可恢复的失败。
        #
        # 改法：停机时**先置位**本标志，再取消轮询循环、再解决 future。
        #   置位后不再把在途任务判为失败，而是**保留 pending_tasks 并保留 future 未决**
        #   （见 `stop()` 的实现注释：作废 ≠ 判失败）。这样：
        #     - 没有错误回调发出 → Java 任务保持非终态 → 重启后 recovery 能接管；
        #     - 本进程内也不会有人误用「已判失败」的 future 结果；
        #     - 轮询循环与 future 都留空，进程退出即消失，无泄漏。
        self._shutting_down = False

    @property
    def shutting_down(self) -> bool:
        """是否正在优雅停机（在途 future 必须按「未决」处理，不得判失败）。"""
        return self._shutting_down

    async def start(self) -> None:
        """启动后台轮询循环。"""
        # ★ 2026-09-19 修（#1）：start 必须清掉停机标志。停机后再 start（测试/热重启）
        #   若标志仍是 True，会把正常轮询任务也按「停机中」处理（不判失败），
        #   等于把轮询静默废掉。
        self._shutting_down = False
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("VideoPoller 启动")

    async def stop(self) -> None:
        """优雅停止：取消轮询循环；**在途任务保留为未决**，绝不判失败。

        ★ 2026-09-19 修（#1）：原实现是「取消循环 + 对每个在途 future
        `set_exception(TimeoutError('服务关闭超时'))` + 发 error 事件 + 清空表」。

        为什么那是错的（链条完整、每一步都在本仓代码里）：
          1. future 被判异常 → `nodes/video.py` 的 gather(return_exceptions=True)
             把它收进 error_msgs，该段没有产物；
          2. 若整会话都还在等视频 → `video_urls` 为空 → `notify_final`
             （nodes/notify_final.py:73-75）判定 **failed** 并回调 Java；
          3. Java `NotifyServiceImpl.handleCompletion` 把任务落成**终态 failed**；
          4. 进程重启 → `recovery.recover_session` 用 `probe_session_tracked` 探测，
             拿到 tracked=false（已终态）→ **跳过恢复**（recovery.py:219-223）；
          5. 结果：优雅停机（Ctrl+C）反而把**本可续跑**的会话钉死成失败，
             而 kill -9 因为不执行 stop()，什么都不发 → 任务留在非终态 →
             看门狗转 interrupted → 重启后恢复接管。这就是「优雅比硬杀更差」。

        现在的语义（关键区别：**作废 ≠ 判失败**）：
          - 置位 `_shutting_down`（供节点/测试判断「本进程已不负责在途任务」）；
          - 取消轮询循环（不再发任何 progress/error 事件）；
          - 在途 future **保持未决**：不发错误回调 → Java 侧任务停在非终态 →
            重启后 `recovery` 能按 `progress.submitted` 里的 video_id 零成本捞回；
          - `pending_tasks` 也保留（便于诊断/测试断言），进程退出即随内存消失，
            不会泄漏到下一次运行。
        """
        self._shutting_down = True
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for video_id, task in list(self.pending_tasks.items()):
            if task["future"].done():
                continue
            # ⚠️ 这里**绝不能** set_exception / set_result：任何「解决」都会被
            #    gather 当成终局结果，进而触发上面第 2-4 步的终态回调。
            #    只记日志说明「本进程不再负责它」。
            logger.warning(
                "优雅停机：video_id=%s session=%s shot=%s 仍在生成，保留为未决，"
                "交由下次启动的 recovery 续跑（不发失败回调）",
                video_id, task.get("session_id"), task.get("shot_index"),
            )
        logger.info("VideoPoller 已停止（在途 %d 个任务保留为未决，未判失败）",
                    len(self.pending_tasks))

    async def submit(self, video_id: str, model_name: str,
                     session_id: str, shot_index: int,
                     provider: str = "intl") -> asyncio.Future:
        """注册一个新提交的任务，返回对应的 Future。provider 用于后续查询路由。

        ⚠️ 停机中（`shutting_down`）调用本方法**不会**再被轮询：future 会永远未决。
        这是刻意的（与 `stop()` 同一语义：未决 = 交给重启后的 recovery 续跑），
        调用方不该在停机窗口提交新任务 —— 真要提交，应在启动恢复流程里做。
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.pending_tasks[video_id] = {
            "model_name": model_name,
            "session_id": session_id,
            "shot_index": shot_index,
            "future": future,
            "submitted_at": time.time(),
            "last_progress": -1,
            "provider": provider,
        }
        logger.info(
            "VideoPoller 注册任务: video_id=%s shot=%d provider=%s",
            video_id, shot_index, provider,
        )
        return future

    def get_future(self, video_id: str) -> asyncio.Future | None:
        """根据 video_id 获取 Future。"""
        task = self.pending_tasks.get(video_id)
        return task["future"] if task else None

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        while self._running:
            await asyncio.sleep(settings.poll_interval_s)
            if not self.pending_tasks:
                continue
            for video_id, task in list(self.pending_tasks.items()):
                if task["future"].done():
                    continue
                await self._check_task(video_id, task)

    async def _check_task(self, video_id: str, task: dict) -> None:
        """检查单个任务状态。provider 来自提交时记录（保证查询与提交同账号）。

        ★ 2026-09-19 修（#8）：**查询侧失败 ≠ 任务失败**。
        改前形状：整段包在一个 `try/except Exception` 里，`except` 分支对 future
        `set_exception(e)`、从 pending 表摘除、发 error 事件 —— 于是一次**查询**
        出错（上游 401/404/5xx、网关 `query_video` 抛错、JSON 解析失败、
        甚至 provider 选错账号）就把这一镜**永久判失败**，已完成的产物被丢弃。
        上游 401/404 的常见成因与「任务真的失败」毫无关系：API key 轮换、
        账号归属不符、video_id 在本账号查不到（多 provider 下很常见）。
        改法：把「查询没成功」与「查询成功但状态为失败」分开 ——
          - 查询抛错 → **只记日志、保留在 pending 表**，下一轮（5s 后）再查；
            若一直查不通，`video_timeout_s` 的超时分支仍会兜底（不会永久挂着）。
          - 查询成功且 body 明确 failed/error → 才判失败（原逻辑不变）。
        """
        try:
            result = await gateway.query_video(
                video_id, task["model_name"],
                provider_name=task.get("provider", "intl"),
            )
        except Exception as e:
            # 查询失败：保留任务、不判失败（下一轮重试，最终由超时兜底）
            logger.warning(
                "VideoPoller 查询失败（保留任务待下轮重试，不判失败）video_id=%s: %s",
                video_id, e,
            )
            return
        try:
            # 进度事件
            progress = result.get("progress")
            if isinstance(progress, (int, float)):
                p = int(progress)
                if p != task["last_progress"]:
                    task["last_progress"] = p
                    from app import events
                    await events.emit(
                        task["session_id"], "progress",
                        {"progress": p}
                    )

            # 超时检查
            if time.time() - task["submitted_at"] > settings.video_timeout_s:
                task["future"].set_exception(
                    TimeoutError(f"video {video_id} 轮询超时")
                )
                from app import events
                await events.emit(
                    task["session_id"], "error",
                    {"error": f"video {video_id} 轮询超时"}
                )
                del self.pending_tasks[video_id]
                return

            # 完成判定
            video_url = result.get("url") or result.get("video_url")
            status = result.get("status")
            if video_url and status in ("completed", "done"):
                task["future"].set_result(
                    {"video_url": video_url, "video_id": video_id}
                )
                del self.pending_tasks[video_id]
                # 仅发射 SSE 事件，Java 回调由 nodes/video.py 统一处理
                from app import events
                await events.emit(
                    task["session_id"], "shot_completed",
                    {"video_id": video_id, "shot_index": task["shot_index"], "video_url": video_url}
                )
                logger.info("VideoPoller 任务完成: video_id=%s", video_id)
                return

            # 失败判定
            # ★ 2026-09-19 修（#8）：只有**明确的状态**才算任务失败。
            #   改前条件 `result.get("error") or status in (...)`：`error` 键单独成立 ——
            #   而上游的「账号/归属不符」「任务不在本账号」也走 `{"error": {...}}` 形状
            #   （recovery._probe_video 的注释里记着同一个坑，那边今天已改为不认 error 键）。
            #   一次 401/404 的 body 就足以把**还在生成、甚至已生成完**的这一镜判失败。
            #   现在：body 带 error 但**没有**明确的 failed 状态 → 只记日志、保留待下轮重试；
            #   只有 status ∈ {failed, error} 才判失败。
            status_lower = str(status or "").strip().lower()
            if status_lower in ("failed", "error"):
                err_msg = result.get("error") or f"status={status}"
                task["future"].set_exception(
                    RuntimeError(f"video {video_id} 生成失败: {err_msg}")
                )
                from app import events
                await events.emit(
                    task["session_id"], "error",
                    {"error": f"video {video_id} 生成失败: {err_msg}"}
                )
                del self.pending_tasks[video_id]
                logger.warning("VideoPoller 任务失败: video_id=%s", video_id)
            elif result.get("error"):
                # 错误体但没有明确状态：多为账号/归属/查询侧问题，**不是**任务失败
                logger.warning(
                    "VideoPoller 收到错误体但无明确失败状态（保留任务待下轮重试）"
                    "video_id=%s err=%s", video_id, result.get("error"),
                )

        except Exception as e:
            # 兜底：判定/事件逻辑自身的异常。**保留任务**（不再判失败），
            # 否则一次事件总线抖动就能把在途镜次打成永久失败。
            logger.error("VideoPoller 状态判定异常（保留任务）video_id=%s: %s",
                         video_id, e)


# 全局单例
poller = VideoPoller()
