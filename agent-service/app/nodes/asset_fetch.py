"""LangGraph 节点：asset_fetch（视频产物落地到本地）。

**为什么单独成节点，而不是放在 synthesizer 里（P0-1 的核心）**

QC 只能对本地文件跑 cv2 / ffmpeg，而 agnes 返回的是公网直链。原架构里唯一的下载
发生在 `synthesizer_node` 内部，而**标准模式根本不经过 synthesizer**
（`video_generator → qc_checker → END`）——于是 `qc_checker` 面对的永远是 http 直链，
只能走 `skipped=True, passed=True` 返回。**QC 在生产环境从未真正跑过一次。**

下载前置为独立节点后：
- `qc_checker` 有本地文件可检
- `synthesizer` 直接吃本地文件，不再重复下载（画布模式下载次数 2 → 1）
- 后续 `fix_looping` 的「只重生失败镜」也有了前提

**索引对齐硬约束**

`local_video_paths` 与 `video_urls` 严格同长同序；下载失败的索引留空串占位，
**不压缩数组**。否则下游按索引取段会整体错位 —— 这正是历史上「段索引错位」
那类 bug 的根源（见提交 949cf41）。空串由 `qc_checker` 识别为「产物缺失」。
"""
import logging

from app.state import CreativeSessionState, TaskStatus
from app.utils.media import download, session_dir

logger = logging.getLogger(__name__)


async def asset_fetch_node(state: CreativeSessionState) -> dict:
    from app import events
    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "asset_fetch", "node_name": "产物本地化"})

    video_urls = list(state.get("video_urls") or [])
    if not video_urls:
        logger.warning("asset_fetch: 无视频可下载（session=%s）", session_id)
        return {"local_video_paths": [], "status": TaskStatus.ASSET_GENERATING}

    shot_dir = session_dir(session_id)
    paths: list[str] = []
    downloaded = 0

    for idx, url in enumerate(video_urls):
        dest = shot_dir / f"seg_{idx:03d}.mp4"
        # 已存在且非空 → 复用（恢复/重入场景不重复下载；0 字节残留视为缺失）
        if dest.exists() and dest.stat().st_size > 0:
            paths.append(str(dest))
            logger.info("asset_fetch: 复用已有文件 %s", dest.name)
            continue

        url = str(url or "").strip()
        if not url:
            logger.warning("asset_fetch: 第 %d 段 URL 为空，留空占位", idx)
            paths.append("")
            continue

        try:
            await download(url, dest)
        except Exception as exc:  # 单段失败不阻断整体（与 synthesizer 的降级哲学一致）
            logger.warning("asset_fetch: 第 %d 段下载失败: %s", idx, exc)
            paths.append("")
            continue

        if not dest.exists() or dest.stat().st_size <= 0:
            logger.warning("asset_fetch: 第 %d 段下载后文件为空", idx)
            paths.append("")
            continue

        paths.append(str(dest))
        downloaded += 1
        await events.emit(session_id, "progress", {
            "progress": int((idx + 1) / len(video_urls) * 100),
            "phase": f"下载产物 {idx + 1}/{len(video_urls)}",
        })

    ok_count = sum(1 for p in paths if p)
    logger.info("asset_fetch: 完成 %d/%d 段（本次下载 %d 段）",
                ok_count, len(video_urls), downloaded)
    await events.emit(session_id, "node_completed", {
        "node_id": "asset_fetch",
        "summary": f"产物本地化 {ok_count}/{len(video_urls)} 段",
    })

    return {
        "local_video_paths": paths,
        "status": TaskStatus.ASSET_GENERATING,
    }
