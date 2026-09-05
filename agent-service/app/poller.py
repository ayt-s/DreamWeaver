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

    async def start(self) -> None:
        """启动后台轮询循环。"""
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("VideoPoller 启动")

    async def stop(self) -> None:
        """优雅停止：取消轮询循环，标记所有 pending 为超时。"""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for video_id, task in list(self.pending_tasks.items()):
            if not task["future"].done():
                task["future"].set_exception(
                    TimeoutError(f"video {video_id} 服务关闭超时")
                )
                from app import events
                await events.emit(
                    task["session_id"], "error",
                    {"error": f"video {video_id} 服务关闭超时"}
                )
        self.pending_tasks.clear()
        logger.info("VideoPoller 已停止")

    async def submit(self, video_id: str, model_name: str,
                     session_id: str, shot_index: int,
                     provider: str = "intl") -> asyncio.Future:
        """注册一个新提交的任务，返回对应的 Future。provider 用于后续查询路由。"""
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
        """检查单个任务状态。provider 来自提交时记录（保证查询与提交同账号）。"""
        try:
            result = await gateway.query_video(
                video_id, task["model_name"],
                provider_name=task.get("provider", "intl"),
            )

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
            if result.get("error") or status in ("failed", "error"):
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

        except Exception as e:
            logger.error("VideoPoller 查询异常 video_id=%s: %s", video_id, e)
            task["future"].set_exception(e)
            from app import events
            await events.emit(
                task["session_id"], "error",
                {"error": str(e)}
            )
            del self.pending_tasks[video_id]


# 全局单例
poller = VideoPoller()
