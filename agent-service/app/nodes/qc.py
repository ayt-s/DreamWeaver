"""LangGraph 节点：qc_checker（**逐镜**视频质检）。

## 为什么重写（P0-1）

原实现两个致命缺陷，合起来导致**质检在生产环境从未真正执行过一次**：

1. 判定入口是
   `if video_path.startswith(("http://","https://")) or not os.path.exists(path):`
   → 直接返回 `{"passed": True, "skipped": True}`。而 `video_urls` 全部是 agnes
   公网直链、全链路又没有下载步骤，于是**永远走这个分支**
2. 只检查 `video_urls[0]` —— 多镜任务只看第一镜

A1~A4 把「下载到本地」前置成 `asset_fetch` 节点后，QC 才第一次拿到本地文件。
本节点因此改为：读 `state["local_video_paths"]`，**逐镜**出报告，删除 `skipped` 分支。

## 规则层（全部本地、零成本、可进 CI）

- 黑帧比例 / 模糊帧比例：复用 `app/tools/qc.py` 的 cv2 实现
- 时长偏离：ffmpeg 探测 vs storyboard 期望，容差 `DURATION_TOLERANCE_S`
- 画幅不符：ffmpeg 探测 vs storyboard 期望的 `aspect_ratio`

## 明确的边界（不做什么）

- **不做语义判分**（「分镜是否覆盖剧本要素」）。那需要 LLM，属于 L2 语义层，
  走 LangSmith / 后续任务，不塞进这个纯本地节点。
- **探测失败不作为否决项**：时长/画幅探测返回 -1 时只记日志，不把好镜判成失败。
  探测是附加信息，缺了不该否决整个镜。
- 单镜异常不中断其它镜（与 `synthesizer` 的降级哲学一致）。

## 阈值

`app/tools/qc.py` 里的黑帧/模糊阈值**尚未按真实产物标定**（见 Task A6）：
Laplacian 方差低 ≠ 人眼觉得模糊（浅景深 / 柔光 / 纯色背景 / 特写人脸天然低方差）。
在 A6 完成前，本节点的判定结果应当视为「参考」而非「结论」。
"""
import asyncio
import logging
from pathlib import Path

from app.state import CreativeSessionState, TaskStatus
from app.tools.qc import analyze_video_frames
from app.utils.media import probe_dimensions, probe_duration

logger = logging.getLogger(__name__)

# 时长容差（秒）：agnes 秒数是整数档位，实测与请求值通常相差 < 1s
DURATION_TOLERANCE_S = 1.0
# 画幅容差（宽高比的绝对差）：16:9=1.778 / 9:16=0.5625，0.08 足以区分两者而不误杀
ASPECT_RATIO_TOLERANCE = 0.08


def _err_text(exc: BaseException) -> str:
    """异常描述兜底：部分异常 str() 为空，退回类型名，避免错误信息退化成空串。"""
    return str(exc).strip() or type(exc).__name__


def _to_int(value) -> int | None:
    """把 storyboard 里的 seconds（可能是 "5" 或 5）转成 int；无法解析返回 None。"""
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _aspect_matches(w: int, h: int, expected: str) -> bool:
    """实测分辨率是否与期望画幅（如 "16:9"）匹配；期望值无法解析时不否决。"""
    try:
        ew_s, eh_s = str(expected).replace("：", ":").split(":")
        ew, eh = int(ew_s), int(eh_s)
    except (TypeError, ValueError):
        return True
    if ew <= 0 or eh <= 0 or h <= 0:
        return True
    return abs((w / h) - (ew / eh)) <= ASPECT_RATIO_TOLERANCE


async def _check_one(idx: int, path: str, shot: dict) -> dict:
    """检查单镜，返回该镜的报告条目（**不抛异常**）。"""
    expected_seconds = _to_int(shot.get("seconds"))
    expected_aspect = str(shot.get("aspect_ratio") or "").strip()
    entry = {
        "index": idx,
        "path": path,
        "total_frames": 0,
        "black_frame_ratio": 0.0,
        "blur_frame_ratio": 0.0,
        "duration": -1.0,
        "duration_expected": expected_seconds,
        "aspect_ratio": expected_aspect,
        "passed": False,
        "error": "",
    }

    # 产物缺失（asset_fetch 下载失败留下的空串占位）—— 这一步必须显式区分，
    # 否则「没检查」会被误当成「检查通过」
    if not path:
        entry["error"] = "产物缺失，未下载成功"
        return entry
    if not Path(path).exists():
        entry["error"] = "本地文件不存在"
        return entry

    errors: list[str] = []

    # 1) cv2 规则：黑帧 / 模糊（同步阻塞调用放线程里，避免堵事件循环）
    try:
        report = await asyncio.to_thread(analyze_video_frames, path)
        if isinstance(report, dict):
            entry["total_frames"] = report.get("total_frames", 0)
            entry["black_frame_ratio"] = report.get("black_frame_ratio", 0.0)
            entry["blur_frame_ratio"] = report.get("blur_frame_ratio", 0.0)
            if not report.get("passed", False):
                errors.append(
                    "画面质检未通过（黑帧比例 {:.0%}，模糊帧比例 {:.0%}）".format(
                        float(entry["black_frame_ratio"] or 0.0),
                        float(entry["blur_frame_ratio"] or 0.0),
                    )
                )
        else:
            errors.append("画面质检返回非法结果")
    except Exception as exc:
        errors.append(f"画面质检异常: {_err_text(exc)}")

    # 2) 时长规则（探测失败 → 只记日志，不否决）
    try:
        duration = await probe_duration(path)
    except Exception as exc:
        logger.debug("qc_checker: 时长探测异常 path=%s: %s", path, exc)
        duration = -1.0
    entry["duration"] = duration
    if duration > 0 and expected_seconds is not None:
        if abs(duration - expected_seconds) > DURATION_TOLERANCE_S:
            errors.append(
                f"时长偏离（期望 {expected_seconds}s，实测 {duration:.1f}s）"
            )

    # 3) 画幅规则（同上，探测失败不否决）
    try:
        width, height = await probe_dimensions(path)
    except Exception as exc:
        logger.debug("qc_checker: 画幅探测异常 path=%s: %s", path, exc)
        width, height = (-1, -1)
    if width > 0 and height > 0 and expected_aspect:
        if not _aspect_matches(width, height, expected_aspect):
            errors.append(f"画幅不符（期望 {expected_aspect}，实测 {width}x{height}）")

    entry["passed"] = not errors
    entry["error"] = "; ".join(errors)
    return entry


async def qc_checker_node(state: CreativeSessionState) -> dict:
    from app import events
    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "qc_checker", "node_name": "质检检查"})

    paths = [str(p or "") for p in (state.get("local_video_paths") or [])]
    storyboard = state.get("storyboard") or []

    if not paths:
        # 显式失败，而不是「无文件可检 → 通过」。这正是原实现最大的坑。
        report = {
            "passed": False,
            "shots": [],
            "failed_shots": [],
            "total_shots": 0,
            "reason": "无本地视频文件可检查（asset_fetch 未产出产物）",
        }
        await events.emit(session_id, "tool_called",
                          {"tool_name": "qc_check", "report": report})
        return {"qc_report": report, "status": TaskStatus.QC_CHECKING}

    shots: list[dict] = []
    for idx, path in enumerate(paths):
        shot = storyboard[idx] if idx < len(storyboard) and isinstance(storyboard[idx], dict) else {}
        shots.append(await _check_one(idx, path, shot))

    failed = [s["index"] for s in shots if not s["passed"]]
    passed = not failed
    report = {
        "passed": passed,
        "shots": shots,
        "failed_shots": failed,
        "total_shots": len(paths),
        "reason": "" if passed else f"{len(failed)}/{len(paths)} 镜未通过质检",
    }

    await events.emit(session_id, "tool_called",
                      {"tool_name": "qc_check", "report": report})
    await events.emit(session_id, "node_completed", {
        "node_id": "qc_checker",
        "summary": ("质检通过 %d/%d 镜" % (len(paths) - len(failed), len(paths)))
        if passed else report["reason"],
    })
    logger.info("qc_checker: passed=%s failed_shots=%s", passed, failed)

    return {"qc_report": report, "status": TaskStatus.QC_CHECKING}
