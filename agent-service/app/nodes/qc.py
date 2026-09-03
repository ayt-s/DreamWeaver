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

    # 对第一个视频进行 QC 分析（可扩展为全部）
    qc_report = analyze_video_frames(str(video_urls[0]))

    await events.emit(state["session_id"], "tool_called",
                      {"tool_name": "qc_check", "report": qc_report})

    return {
        "qc_report": qc_report,
        "status": TaskStatus.QC_CHECKING,
    }
