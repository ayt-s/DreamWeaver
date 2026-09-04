"""LangGraph 图定义（Phase 4 P0：文生图 + 图生视频贯通；画布模式多镜拼接）。

三种入口：
- 无限画布图生视频（segments 非空）：canvas_storyboarder → video_generator → synthesizer → END
- 标准文生视频/图生视频（segments 为空）：requirement_parser → script_writer → storyboarder
  → image_generator → video_generator → qc_checker → END
- 文生图模式：image_generator 之后直达 END（只出图不出视频）

无限画布模式说明：用户上传 N 张图片并逐段描述内容（segments），
每段生成几秒小视频，最后由 synthesizer 用 ffmpeg 拼接成一条长视频。
"""
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.state import CreativeSessionState, TaskStatus
from app.nodes.parser import requirement_parser_node
from app.nodes.script import script_writer_node
from app.nodes.storyboard import storyboarder_node, canvas_storyboarder_node
from app.nodes.image import image_generator_node
from app.nodes.video import video_generator_node
from app.nodes.synthesizer import synthesizer_node
from app.nodes.qc import qc_checker_node


def _fix_looping_node(state: CreativeSessionState) -> dict:
    """Fix looping 节点（Phase 2 stub）：暂直接返回，后续接入修复逻辑。"""
    return {"status": state.get("status", None)}


def _entry_route(state: CreativeSessionState) -> str:
    """入口路由：画布模式（segments 非空）跳过需求解析/剧本/分镜 LLM 环节。"""
    if state.get("segments"):
        return "canvas"
    return "standard"


def _qc_route(state: CreativeSessionState) -> str:
    """根据 QC 报告决定路由。"""
    qc_report = state.get("qc_report", {})
    if qc_report.get("passed", False):
        return "qc_passed"
    return "qc_failed"


def _image_route(state: CreativeSessionState) -> str:
    """image_generator 之后的路线：文生图只出图不出视频，其余继续视频链路。"""
    if state.get("gen_type") == "text_image":
        return "text_done"
    return "to_video"


def _video_route(state: CreativeSessionState) -> str:
    """video_generator 之后的分流：画布模式（segments）→ synthesizer 拼接；否则走 QC。"""
    if state.get("segments"):
        return "synthesize"
    return "qc"


graph = StateGraph(CreativeSessionState)

graph.add_node("requirement_parser", requirement_parser_node)
graph.add_node("script_writer", script_writer_node)
graph.add_node("storyboarder", storyboarder_node)
graph.add_node("canvas_storyboarder", canvas_storyboarder_node)
graph.add_node("image_generator", image_generator_node)
graph.add_node("video_generator", video_generator_node)
graph.add_node("qc_checker", qc_checker_node)
graph.add_node("synthesizer", synthesizer_node)
graph.add_node("fix_looping", _fix_looping_node)  # Phase 2 stub

# === 入口路由 ===
graph.set_conditional_entry_point(
    _entry_route,
    {"canvas": "canvas_storyboarder", "standard": "requirement_parser"},
)

# === 标准链路 ===
graph.add_edge("requirement_parser", "script_writer")
graph.add_edge("script_writer", "storyboarder")
graph.add_edge("storyboarder", "image_generator")

# 文生图模式：image_generator 后直达 END（只出图不出视频）；
# 其余模式继续 video_generator
graph.add_conditional_edges(
    "image_generator",
    _image_route,
    {"text_done": END, "to_video": "video_generator"},
)

# === 画布模式：用户自定分镜，跳过剧本/分镜/生图，直接生成视频再拼接 ===
graph.add_edge("canvas_storyboarder", "video_generator")

# video_generator 后分流：画布模式 → synthesizer 拼接长视频；标准模式 → QC
graph.add_conditional_edges(
    "video_generator",
    _video_route,
    {"synthesize": "synthesizer", "qc": "qc_checker"},
)
graph.add_edge("synthesizer", END)

# === 标准链路：video_generator → qc_checker ===
# （上面已由 _video_route 接入；此处仅为可读性保留注释）

# QC 结果分支
graph.add_conditional_edges(
    "qc_checker",
    _qc_route,
    {"qc_passed": END, "qc_failed": "fix_looping"},
)

# MemorySaver 开发用；生产换 PostgresSaver（设计文档 §4.1）
checkpointer = MemorySaver()
compiled_graph = graph.compile(checkpointer=checkpointer)