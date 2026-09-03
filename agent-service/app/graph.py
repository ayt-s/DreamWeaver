"""LangGraph 图定义（Phase 4 P0：文生图 + 图生视频贯通）。

新流水线：
requirement_parser → script_writer → storyboarder → image_generator → video_generator → qc_checker → END

image_generator 在 storyboarder 之后，每个镜次用 prompt_en 逐镜生图，
图 URL 回填到对应 shot.reference_images，视频生成时自动走 mode="image"。
"""
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.state import CreativeSessionState, TaskStatus
from app.nodes.parser import requirement_parser_node
from app.nodes.script import script_writer_node
from app.nodes.storyboard import storyboarder_node
from app.nodes.image import image_generator_node
from app.nodes.video import video_generator_node
from app.nodes.qc import qc_checker_node


def _fix_looping_node(state: CreativeSessionState) -> dict:
    """Fix looping 节点（Phase 2 stub）：暂直接返回，后续接入修复逻辑。"""
    return {"status": state.get("status", None)}


def _qc_route(state: CreativeSessionState) -> str:
    """根据 QC 报告决定路由。"""
    qc_report = state.get("qc_report", {})
    if qc_report.get("passed", False):
        return "qc_passed"
    return "qc_failed"


def _image_route(state: CreativeSessionState) -> str:
    """image_generator 之后的路线：小说转图只出图不出视频，其余继续视频链路。"""
    if state.get("gen_type") == "novel_image":
        return "novel_done"
    return "to_video"


graph = StateGraph(CreativeSessionState)

graph.add_node("requirement_parser", requirement_parser_node)
graph.add_node("script_writer", script_writer_node)
graph.add_node("storyboarder", storyboarder_node)
graph.add_node("image_generator", image_generator_node)
graph.add_node("video_generator", video_generator_node)
graph.add_node("qc_checker", qc_checker_node)
graph.add_node("fix_looping", _fix_looping_node)  # Phase 2 stub

graph.set_entry_point("requirement_parser")
graph.add_edge("requirement_parser", "script_writer")
graph.add_edge("script_writer", "storyboarder")
graph.add_edge("storyboarder", "image_generator")

# 小说转图模式：image_generator 后直达 END（只出图不出视频）；
# 其余模式继续 video_generator → qc_checker
graph.add_conditional_edges(
    "image_generator",
    _image_route,
    {"novel_done": END, "to_video": "video_generator"},
)
graph.add_edge("video_generator", "qc_checker")

# QC 结果分支
graph.add_conditional_edges(
    "qc_checker",
    _qc_route,
    {"qc_passed": END, "qc_failed": "fix_looping"},
)

# MemorySaver 开发用；生产换 PostgresSaver（设计文档 §4.1）
checkpointer = MemorySaver()
compiled_graph = graph.compile(checkpointer=checkpointer)
