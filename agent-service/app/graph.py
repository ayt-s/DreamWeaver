"""LangGraph 图定义（Phase 1 MVP：线性主链路 + Phase 2 QC 节点）。

requirement_parser → script_writer → storyboarder → video_generator → qc_checker → END
                                                    ↘ fix_looping (stub)
"""
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.state import CreativeSessionState
from app.nodes.parser import requirement_parser_node
from app.nodes.script import script_writer_node
from app.nodes.storyboard import storyboarder_node
from app.nodes.video import video_generator_node
from app.nodes.qc import qc_checker_node

graph = StateGraph(CreativeSessionState)

graph.add_node("requirement_parser", requirement_parser_node)
graph.add_node("script_writer", script_writer_node)
graph.add_node("storyboarder", storyboarder_node)
graph.add_node("video_generator", video_generator_node)
graph.add_node("qc_checker", qc_checker_node)
graph.add_node("fix_looping", _fix_looping_node)  # Phase 2 stub

graph.set_entry_point("requirement_parser")
graph.add_edge("requirement_parser", "script_writer")
graph.add_edge("script_writer", "storyboarder")
graph.add_edge("storyboarder", "video_generator")
graph.add_edge("video_generator", "qc_checker")

# QC 结果分支
graph.add_conditional_edges(
    "qc_checker",
    _qc_route,
    {"qc_passed": END, "qc_failed": "fix_looping"},
)


def _fix_looping_node(state: CreativeSessionState) -> dict:
    """Fix looping 节点（Phase 2 stub）：暂直接返回，后续接入修复逻辑。"""
    return {"status": state.get("status", None)}


def _qc_route(state: CreativeSessionState) -> str:
    """根据 QC 报告决定路由。"""
    qc_report = state.get("qc_report", {})
    if qc_report.get("passed", False):
        return "qc_passed"
    return "qc_failed"

# MemorySaver 开发用；生产换 PostgresSaver（设计文档 §4.1）
checkpointer = MemorySaver()
compiled_graph = graph.compile(checkpointer=checkpointer)