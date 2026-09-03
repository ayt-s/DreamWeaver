"""LangGraph 图定义（Phase 1 MVP：线性主链路）。

requirement_parser → script_writer → storyboarder → video_generator → (synthesizer stub)
QC / fix_loop / 断点恢复 属 Phase 2/3，暂不接入。
"""
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.state import CreativeSessionState
from app.nodes.parser import requirement_parser_node
from app.nodes.script import script_writer_node
from app.nodes.storyboard import storyboarder_node
from app.nodes.video import video_generator_node

graph = StateGraph(CreativeSessionState)

graph.add_node("requirement_parser", requirement_parser_node)
graph.add_node("script_writer", script_writer_node)
graph.add_node("storyboarder", storyboarder_node)
graph.add_node("video_generator", video_generator_node)

graph.set_entry_point("requirement_parser")
graph.add_edge("requirement_parser", "script_writer")
graph.add_edge("script_writer", "storyboarder")
graph.add_edge("storyboarder", "video_generator")
graph.add_edge("video_generator", END)

# MemorySaver 开发用；生产换 PostgresSaver（设计文档 §4.1）
checkpointer = MemorySaver()
compiled_graph = graph.compile(checkpointer=checkpointer)