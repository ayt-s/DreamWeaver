"""LangGraph 节点：synthesizer（多镜拼接长视频）。

无限画布模式（segments 存在）下，video_generator 产出的是每段几秒的小视频，
本节点把它们下载到本地，用 ffmpeg 拼成一条长视频，输出到
<本地输出目录>/<session>/final.mp4，并经 FastAPI 静态目录（/v1/files）对外可访问。

单段视频也走这里：拷贝为 final.mp4（不拼接），保证产物字段统一。

失败处理：任一环节失败不阻断任务——降级为直接透传原 video_urls。

**A1 起：下载/时长探测/拼接已抽到 `app/utils/media.py`**（asset_fetch 与 image_slideshow
  都要用，放本节点里会形成「节点 import 节点」的横向依赖）。
  导入方式固定为 `from app.utils.media import ...`（绑定进本模块命名空间），
  因为测试用 `monkeypatch.setattr(syn_mod, "download", ...)` 打桩。
"""
import asyncio
import logging

from app.state import CreativeSessionState, TaskStatus
from app.utils.media import concat_videos, download, local_url, session_dir

logger = logging.getLogger(__name__)


def _error_text(exc: BaseException) -> str:
    """异常描述兜底：部分异常 str() 为空（如 SelectorEventLoop 下的 NotImplementedError），
    直接用类型名，避免错误消息退化成空串而被上层静默丢弃。"""
    return str(exc).strip() or type(exc).__name__


async def _notify_final(session_id: str, status: str, video_urls: list[str],
                        error_message: str | None = None) -> None:
    """画布模式最终完成回调：携带拼接后的长视频 URL（放首位）+ 各分段 URL。"""
    from app.callback.java_notify import notify_java_completion
    asyncio.create_task(
        notify_java_completion(
            video_id="",
            session_id=session_id,
            shot_index=None,
            status=status,
            video_urls=video_urls,
            error_message=error_message,
        )
    )


async def synthesizer_node(state: CreativeSessionState) -> dict:
    from app import events
    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "synthesizer", "node_name": "多镜拼接"})

    video_urls = list(state.get("video_urls", []))
    if not video_urls:
        logger.warning("synthesizer: 无视频可拼接")
        await events.emit(session_id, "node_completed",
                          {"node_id": "synthesizer", "summary": "无视频，跳过拼接"})
        return {"status": TaskStatus.COMPLETED, "final_video_url": ""}

    shot_dir = session_dir(session_id)
    local_files = []
    try:
        for i, url in enumerate(video_urls):
            dest = shot_dir / f"seg_{i:03d}.mp4"
            await download(url, dest)
            local_files.append(dest)
            await events.emit(session_id, "progress",
                              {"progress": int((i + 1) / len(video_urls) * 50), "phase": "下载分段"})
            logger.info("synthesizer: 下载分段 %d/%d → %s", i + 1, len(video_urls), dest.name)

        final_mp4 = shot_dir / "final.mp4"
        ok = await concat_videos(local_files, final_mp4)
        if not ok:
            raise RuntimeError("ffmpeg 拼接失败")

        final_url = local_url(session_id)
        await events.emit(session_id, "progress",
                          {"progress": 100, "phase": "拼接完成"})
        logger.info("synthesizer: 长视频生成 %s (%d bytes)", final_url, final_mp4.stat().st_size)
        await events.emit(session_id, "node_completed",
                          {"node_id": "synthesizer", "summary": f"拼接 {len(video_urls)} 段为长视频"})

        # 最终回调：长视频在前，分段在后（Java 任务 result_json 全量落库）
        await _notify_final(session_id, "completed", [final_url] + video_urls)
        await events.emit(session_id, "completed", {})
        return {
            "final_video_url": final_url,
            "status": TaskStatus.COMPLETED,
        }
    except Exception as exc:
        # 拼接失败不阻断任务：分段视频仍可用，透传给用户；但原因必须显式带回 Java，
        # 否则 error_message 为空 → 任务显示 completed 却没有成片，用户无从判断（实测故障）
        reason = _error_text(exc)
        logger.error("synthesizer 失败: %s", reason, exc_info=True)
        msg = f"多镜拼接失败（分段视频仍可下载）：{reason}"
        await events.emit(session_id, "error", {"error": msg})
        await events.emit(session_id, "node_completed",
                          {"node_id": "synthesizer", "summary": f"拼接失败，透传 {len(video_urls)} 段"})
        await _notify_final(session_id, "completed", video_urls, error_message=msg)
        await events.emit(session_id, "completed", {})
        return {
            "final_video_url": "",
            "status": TaskStatus.COMPLETED,
        }
