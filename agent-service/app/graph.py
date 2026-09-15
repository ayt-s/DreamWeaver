"""LangGraph 图定义（Phase 4 P0：文生图 + 图生视频贯通；画布模式多镜拼接）。

三种入口：
- 无限画布图生视频（segments 非空）：canvas_storyboarder → video_generator → asset_fetch
  → synthesizer → END
- 标准文生视频/图生视频（segments 为空）：requirement_parser → script_writer → storyboarder
  → image_generator → video_generator → asset_fetch → qc_checker → notify_final → END
- 文生图模式：image_generator 之后直达 END（只出图不出视频）

**notify_final（A9 接入）**：图里**唯一**发终态回调的地方。此前标准模式的回调在
`video_generator` 内发出，位置在 QC **之前** → Java 任务立刻转终态，导致
(1) `NotifyServiceImpl` 的终态检查会丢弃 fix_looping 重生后的回调、
(2) `handleHeartbeat` 对终态任务回 `tracked=false` 而 agent 把它当中止信号 →
自愈循环被自己掐死。回调收敛到终态后这两个问题一并消失。

**asset_fetch（A4 接入）**：video_generator 产出的是 agnes 公网直链，QC 只能检本地文件，
所以必须先把产物落到本地再进 QC。原先下载只发生在 synthesizer 内部，而标准模式
根本不经过 synthesizer —— 于是 qc_checker 拿到的永远是 http 直链，只能 skip 返回 passed，
**质检链路在生产环境从未真正执行过**（P0-1）。

无限画布模式说明：用户上传 N 张图片并逐段描述内容（segments），
每段生成几秒小视频，最后由 synthesizer 用 ffmpeg 拼接成一条长视频。
"""
import logging

from langgraph.graph import END, StateGraph

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
from app.nodes.notify_final import notify_final_node
from app.nodes.fix_looping import fix_looping_node

logger = logging.getLogger(__name__)


def _fix_route(state: CreativeSessionState) -> str:
    """fix_looping 之后：继续修（回到 video_generator）还是放弃（去终态通知）。

    ⚠️ 这里**只读结论，不做二次判断**。闸门逻辑集中在
    `nodes/fix_looping.decide_repair()` —— 两处各写一份必然漂移，
    而漂移的代价很重（两种都实测到过）：

    - 节点空转 + 路由仍 retry → **无限循环**
      （`GraphRecursionError: Recursion limit of 10007`）
    - 节点改了 storyboard 才被路由否决 → **幽灵轮次**，多余 `fix_hint`
      随 segments_json 污染 Java 侧的段重生基线
    """
    return "give_up" if state.get("fix_give_up") else "retry"


async def _fix_give_up_node(state: CreativeSessionState) -> dict:
    """放弃自动修复：把「修过但没修好」如实记下来，交给 notify_final 上报。

    **不标 failed**：已通过的镜仍是可用产物（与 synthesizer 的降级哲学一致）。
    Java 侧会收到 `completed` + error_message 说明未通过情况。
    """
    from app import events

    session_id = state["session_id"]
    rounds = len(state.get("fix_history") or [])
    qc_report = state.get("qc_report") or {}
    failed = list(qc_report.get("failed_shots") or [])
    # 优先用 decide_repair 给出的具体原因（成本闸门 / 轮次用尽 / 已中止），
    # 它比这里重新拼一句更准确 —— 避免又出现「两处各写一份」的漂移。
    if state.get("fix_aborted"):
        reason = "会话已中止（任务被删除或重新生成）"
    elif rounds:
        reason = (f"{state.get('fix_give_up_reason') or ''}"
                  f"（已自动修复 {rounds} 轮，仍有 {len(failed)} 镜未通过质检）").lstrip("（")
    else:
        reason = (state.get("fix_give_up_reason")
                  or f"有 {len(failed)} 镜未通过质检，未做自动修复")

    logger.warning("fix_give_up: session=%s %s", session_id, reason)
    await events.emit(session_id, "node_completed",
                      {"node_id": "fix_give_up", "summary": f"放弃修复：{reason}"})

    return {"fix_give_up": True, "fix_give_up_reason": reason}


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
graph.add_node("fix_looping", fix_looping_node)
graph.add_node("fix_give_up", _fix_give_up_node)
graph.add_node("notify_final", notify_final_node)

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

# QC 结果分支：通过 → 终态通知；失败 → fix_looping（B 批次改为真循环）
graph.add_conditional_edges(
    "qc_checker",
    _qc_route,
    {"qc_passed": "notify_final", "qc_failed": "fix_looping"},
)

# fix_looping → 条件边：失败镜可修复则回到 video_generator 继续修（B1/B2 的真循环），
# 否则走 fix_give_up 收尾。四道闸门见 _fix_route。
graph.add_conditional_edges(
    "fix_looping",
    _fix_route,
    {"retry": "video_generator", "give_up": "fix_give_up"},
)
graph.add_edge("fix_give_up", "notify_final")

# 图的唯一终态出口：恰好发一次完成回调（A9）
graph.add_edge("notify_final", END)

# 刻意**不装 checkpointer**：恢复统一走 session_store.py 的 Redis 快照 + recovery.py。
#
# 为什么删掉 MemorySaver（原先写着「开发用，生产换 PostgresSaver」）：
#   1. **零功能**：全仓 grep `get_state` / `update_state` / `get_state_history` 零命中，
#      没有任何地方做「从 checkpoint 恢复」；recovery.py 是「重建 state + 从入口重新入队」，
#      完全不碰 checkpointer。
#   2. **粒度不对**：checkpointer 的粒度是「节点边界」，救不了 video_generator 全段并发
#      提交中途被杀的场景（见 session_store.py 顶部注释；video.py 里也是同一句）。
#   3. **无界内存泄漏**：实测跑 300 个不同 thread_id → saver.storage 0 → 300 条，
#      **永不被清理**，每条含一份完整 state 副本。生产上跑得越久占用越大。
#
# 实测依据（2026-09-15）：去掉 checkpointer 后跑全量真实测试 → 零回归
# （且那是含 set_conditional_entry_point + 4 处 conditional_edges 的真实图，不是玩具图）。
#
# 若将来需要 interrupt()（human-in-the-loop）或时间旅行调试，再装 —— 那时应配
# 带 TTL 的持久化 saver（如 PostgresSaver），而不是内存版。
compiled_graph = graph.compile()
