"""把某个会话的分段视频拼成一条成片（本地 ffmpeg，不消耗生成额度）。

**为什么单独成一个模块**：拼接此前只有两处调用方，各自为政——

1. `app/nodes/synthesizer.py`（画布模式：生成时自动拼接）
2. `main.py` 的 `POST /v1/tasks/{sid}/concat`（标准模式：用户点「拼接成片」）

标准模式（无 segments）走 `video_generator → asset_fetch → qc_checker → notify_final`，
**没有任何自动拼接环节**，用户只能在画廊里手点一次按钮（实测任务 38/39 至今没有成片，
40/41 的 final.mp4 是手点的产物）。这里把「按 video_urls 找到本地分段 → xfade 拼接」
抽成一份可复用实现，让 `notify_final` 与人工端点走同一条代码路径，
避免第三处再抄一遍（抄出来的必然各自演化）。
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def stitch_session(session_id: str, video_urls: list[str] | None = None,
                         allow_download: bool = True, force: bool = False) -> dict | None:
    """把会话目录下的分段拼成 `final.mp4`。

    分块来源优先级：会话目录里已落地的 `seg_*.mp4`（`asset_fetch` 的产物，画布模式
    已复用同理），本地缺失时按 `video_urls` 顺序补下载（老会话/目录被清理的兜底）。

    幂等：`final.mp4` 已存在且不早于最后一个分段 → 直接返回，不重复编码。
    **但 `force=True` 时必须真的重拼**：拼接逻辑本身会变（实测 2026-09-18 修掉
    「多段成片整条没声音」时，31 条既有成片全是无声的，而幂等短路让它们
    **永远拿不到修复** —— 用户点「拼接成片」只会静默拿到旧文件，
    而重生成分段是要花钱的）。所以给人工入口留一个「重新拼接」。

    :param allow_download: 是否允许网络兜底下载。**notify_final 的自动拼接传 False**：
        那条路径在「任务宣告终态」之前执行，而 download 超时上限是 300s/段 —— URL 一旦
        挂住，任务会长时间停在非终态。自动拼接只用本地已有分段，缺了就不拼
        （用户仍可在画廊点「拼接成片」，那个端点保留下载兜底）。
    :param force: 忽略「已有成片」短路，强制重新编码（覆盖 final.mp4）。

    :return: `{final_url, segment_count, duration, cached}`；可拼接分段不足 2 个时返回 None
             （由调用方决定是报错还是跳过）。
    """
    from app.utils.media import concat_videos, download, local_url, probe_duration, session_dir

    d = session_dir(session_id)
    final = d / "final.mp4"

    def _clips() -> list:
        return sorted(p for p in d.glob("seg_*.mp4") if p.stat().st_size > 0)

    clips = _clips()
    if allow_download and len(clips) < 2 and video_urls:
        # 本地不全 → 按 video_urls 顺序补下载（已是本地产物的跳过）
        for i, u in enumerate(video_urls):
            if not u or str(u).startswith("/v1/files/"):
                continue
            dest = d / f"seg_{i:03d}.mp4"
            if dest.exists() and dest.stat().st_size > 0:
                continue
            try:
                await download(str(u), dest)
            except Exception as exc:
                logger.warning("stitch 下载分段失败 sid=%s idx=%s: %s", session_id, i, exc)
        clips = _clips()

    if len(clips) < 2:
        return None

    if (not force and final.exists() and final.stat().st_size > 0
            and final.stat().st_mtime >= clips[-1].stat().st_mtime):
        return {
            "final_url": local_url(session_id),
            "segment_count": len(clips),
            "duration": await probe_duration(final),
            "cached": True,
        }

    ok = await concat_videos(clips, final)
    if not ok:
        # 编码失败：文件可能被写坏，删掉再抛，避免下次被误判成「已有成片」
        try:
            final.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("视频拼接失败（ffmpeg 编码未成功）")

    duration = await probe_duration(final)
    logger.info("stitch 拼接成片 sid=%s 段数=%s 时长=%.1fs", session_id, len(clips), duration)
    return {
        "final_url": local_url(session_id),
        "segment_count": len(clips),
        "duration": duration,
        "cached": False,
    }


def stitch_enabled() -> bool:
    """自动拼接开关（配置缺失时默认开启；本地 ffmpeg 失败会自行降级，不会阻断任务）。"""
    return bool(getattr(settings, "auto_stitch_enabled", True))
