"""工具层：MCP 风格工具注册区（Phase 1 先直接暴露函数，注册表 Phase 2 上）。

契约（对应设计文档 §3.4，Phase 1 内联轮询版）：
- generate_video_tool：提交 + 等待完成，返回单个 video_url（str）
- state 的读写全部由调用方（video_generator_node）负责
"""
import asyncio
import logging
import time

from app.config import settings
from app.gateway.agnes import gateway

logger = logging.getLogger(__name__)


async def save_polling_task(video_id: str, model_name: str,
                            session_id: str, shot_index: int) -> None:
    """持久化 video_id（Phase 1：内存记录 + 日志；Phase 2 落 DB 供恢复扫描）。"""
    # TODO(Phase 2): 写入 polling_tasks 表（video_id, model_name, session_id, shot_index, status=pending）
    print(f"[polling-task] session={session_id} shot={shot_index} video_id={video_id} model={model_name}")


async def generate_video_tool(prompt: str, seconds: str, mode: str,
                              aspect_ratio: str, reference_images: list,
                              session_id: str, shot_index: int) -> dict:
    """提交视频任务并等待完成，返回该镜生成结果。

    返回契约（2026-09 修复）：{"video_url": str, "video_id": str}
    - video_id 为 Agnes 真实任务 ID（此前伪造 shot_ 前缀，Java 幂等键失真）
    - video_url 为成品视频地址

    Phase 1：节点内联轮询（简化实现，先跑通链路）。
    Phase 2：改为 submit + interrupt 挂起，由独立 VideoPoller 接管轮询。
    """
    t0 = time.time()
    submitted = await gateway.submit_video(
        prompt=prompt,
        seconds=seconds,
        mode=mode,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images or [],
    )
    video_id = submitted["video_id"]
    model_name = submitted["model_name"]

    await save_polling_task(
        video_id=video_id, model_name=model_name,
        session_id=session_id, shot_index=shot_index,
    )

    # 内联轮询：每 poll_interval_s 查一次，超时抛错
    # ⚠️ 必须用 video_id 查询（带 model_name），绝不用 task_id
    waited = 0
    last_progress = -1
    while waited < settings.video_timeout_s:
        await asyncio.sleep(settings.poll_interval_s)
        waited += settings.poll_interval_s
        result = await gateway.query_video(video_id, model_name, mode=mode)

        # 进度事件（SSE 实时轨迹用；progress 为 0-100 百分比，实测字段）
        progress = result.get("progress")
        if isinstance(progress, (int, float)) and int(progress) != last_progress:
            last_progress = int(progress)
            from app import events
            await events.emit(session_id, "progress", {"progress": last_progress})

        # 完成判定：顶层 status=completed + url 有值（实测 2026-09 字段）
        # ⚠️ 实测关键：internal_status 全程停留在 'pending' 不是完成标志，
        #    顶层 status 才流转 in_progress → completed。判定必须用顶层 status。
        video_url = result.get("url") or result.get("video_url")
        status = result.get("status")
        if video_url and status in ("completed", "done"):
            return {"video_url": video_url, "video_id": video_id}

        # 失败判定
        if result.get("error") or status in ("failed", "error"):
            raise RuntimeError(f"video {video_id} 生成失败: {result}")

    raise TimeoutError(f"video {video_id} 轮询超时（{settings.video_timeout_s}s）")