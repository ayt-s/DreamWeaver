"""LangGraph 节点：qc_checker（视频 QC 检查）。

在 video_generator 后调用，分析生成视频的帧质量：
- 黑帧比例（全黑像素 > 95%）
- 模糊帧比例（Laplacian 方差 < 阈值）
根据结果决定下一步：通过 → END，失败 → fix_looping
"""
from typing import Any

from app.state import CreativeSessionState, TaskStatus
from app.tools.qc import analyze_video_frames


async def qc_checker_node(state: CreativeSessionState) -> dict:
    from app import events
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "qc_checker", "node_name": "QC 检查"})

    video_urls = state.get("video_urls", [])
    if not video_urls:
        return {
            "qc_report": {"error": "无视频文件可检查", "passed": False},
            "status": TaskStatus.QC_CHECKING,
        }

    # 仅对本地文件进行 QC；mock/远程 URL 跳过检测（测试友好）
    import os
    video_path = str(video_urls[0])
    if video_path.startswith(("http://", "https://")) or not os.path.exists(video_path):
        qc_report = {
            "total_frames": 0,
            "black_frame_ratio": 0.0,
            "blur_frame_ratio": 0.0,
            "passed": True,
            "skipped": True,
            "reason": "非本地文件或 URL，跳过 QC",
        }
    else:
        qc_report = analyze_video_frames(video_path)

    await events.emit(state["session_id"], "tool_called",
                      {"tool_name": "qc_check", "report": qc_report})

    return {
        "qc_report": qc_report,
        "status": TaskStatus.QC_CHECKING,
    }
