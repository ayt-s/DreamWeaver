"""LangGraph 图定义（Phase 4 P0：文生图 + 图生视频贯通；画布模式多镜拼接）。

三种入口：
- 无限画布图生视频（segments 非空）：canvas_storyboarder → video_generator → asset_fetch
  → synthesizer → END
- 标准文生视频/图生视频（segments 为空）：requirement_parser → script_writer → storyboarder
  → image_generator → video_generator → asset_fetch → qc_checker → END
- 文生图模式：image_generator 之后直达 END（只出图不出视频）

**asset_fetch（A4 接入）**：video_generator 产出的是 agnes 公网直链，QC 只能检本地文件，
所以必须先把产物落到本地再进 QC。原先下载只发生在 synthesizer 内部，而标准模式
根本不经过 synthesizer —— 于是 qc_checker 拿到的永远是 http 直链，只能 skip 返回 passed，
**质检链路在生产环境从未真正执行过**（P0-1）。

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
from app.nodes.asset_fetch import asset_fetch_node
from app.nodes.synthesizer import synthesizer_node
from app.nodes.image_slideshow import image_slideshow_node
from app.nodes.qc import qc_checker_node


def _fix_looping_node(state: CreativeSessionState) -> dict:
    """Fix looping 节点（Phase 2 stub）：暂直接返回，后续接入修复逻辑。"""
    return {"status": state.get("status", None)}


def _entry_route(state: CreativeSessionState) -> str:
    """入口路由：画布模式（segments 非空）跳过需求解析/剧本/分镜 LLM 环节。

    图片重生特殊处理：文生图/漫剧任务带 segments 时，直接跳到 image_generator
    （跳过 canvas_storyboarder），因为图片任务不需要 storyboard 翻译环节。
    图片合成视频：slideshow 非空且带图片列表时直达 slideshow 节点（不消耗 agnes 额度）。
    """
    if state.get("slideshow") and state.get("slideshow_images"):
        return "slideshow"
    if state.get("segments"):
        if state.get("gen_type") in ("text_image", "comic_video"):
            return "image_rework"
        return "canvas"
    return "standard"


def _qc_route(state: CreativeSessionState) -> str:
    """根据 QC 报告决定路由。"""
    qc_report = state.get("qc_report", {})
    if qc_report.get("passed", False):
        return "qc_passed"
    return "qc_failed"


def _image_route(state: CreativeSessionState) -> str:
    """image_generator 之后的路线：合成视频 → slideshow；文生图/漫剧只出图；其余继续视频链路。"""
    if state.get("slideshow"):
        return "slideshow"
    if state.get("gen_type") in ("text_image", "comic_video"):
        return "text_done"
    return "to_video"


def _video_route(state: CreativeSessionState) -> str:
    """video_generator 之后**一律**先进 asset_fetch 把产物落到本地。

    不能让画布模式直连 synthesizer、标准模式直连 qc_checker —— 那样标准模式的
    QC 就只拿到 agnes 公网直链，只能 skip 返回 passed（P0-1 的根因）。
    两种模式的分流改到 asset_fetch 之后（见 _asset_route）。
    """
    return "fetch"


def _asset_route(state: CreativeSessionState) -> str:
    """asset_fetch 之后的分流：画布模式（segments）→ synthesizer 拼接；否则进 QC。"""
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
graph.add_node("asset_fetch", asset_fetch_node)
graph.add_node("qc_checker", qc_checker_node)
graph.add_node("synthesizer", synthesizer_node)
graph.add_node("image_slideshow", image_slideshow_node)
graph.add_node("fix_looping", _fix_looping_node)  # Phase 2 stub

# === 入口路由 ===
graph.set_conditional_entry_point(
    _entry_route,
    {"canvas": "canvas_storyboarder", "standard": "requirement_parser",
     "image_rework": "image_generator", "slideshow": "image_slideshow"},
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

# video_generator 之后一律先进 asset_fetch（产物落地本地），
# 再由 _asset_route 分流：画布模式 → synthesizer 拼接；标准模式 → QC
graph.add_conditional_edges(
    "video_generator",
    _video_route,
    {"fetch": "asset_fetch"},
)
graph.add_conditional_edges(
    "asset_fetch",
    _asset_route,
    {"synthesize": "synthesizer", "qc": "qc_checker"},
)
graph.add_edge("synthesizer", END)
graph.add_edge("image_slideshow", END)

# QC 结果分支
graph.add_conditional_edges(
    "qc_checker",
    _qc_route,
    {"qc_passed": END, "qc_failed": "fix_looping"},
)

# MemorySaver 开发用；生产换 PostgresSaver（设计文档 §4.1）
checkpointer = MemorySaver()
compiled_graph = graph.compile(checkpointer=checkpointer)