"""图路由（A4：video_generator → asset_fetch → qc_checker|synthesizer）。

两层断言：
1. **路由函数的返回值**（单元级）
2. **编译后图的真实边**（结构级，用 compiled_graph.get_graph().edges）
   第 2 层才是关键：路由函数写对了但 conditional_edges 的映射表接错，单测发现不了。
   本仓库已有先例——`fix_looping` 无出边却长期以为它「会回环」。
"""
import pytest

from app.graph import _asset_route, _entry_route, _image_route, _video_route
from app.graph import compiled_graph


def _edges() -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in compiled_graph.get_graph().edges}


# ---------------------------------------------------------------- 路由函数

def test_video_route_always_goes_to_asset_fetch():
    """video_generator 之后必须先进 asset_fetch，不能再直连 qc / synthesizer。"""
    assert _video_route({"segments": [{"prompt": "a"}]}) == "fetch"
    assert _video_route({}) == "fetch"


def test_asset_route_canvas_to_synthesizer():
    assert _asset_route({"segments": [{"prompt": "a"}]}) == "synthesize"


def test_asset_route_standard_to_qc():
    assert _asset_route({}) == "qc"
    assert _asset_route({"segments": []}) == "qc"


def test_entry_and_image_routes_unchanged():
    """A4 不应改动入口与图片路由。"""
    assert _entry_route({"slideshow": True, "slideshow_images": ["a", "b"]}) == "slideshow"
    assert _entry_route({"segments": [{"p": 1}], "gen_type": "text_image"}) == "image_rework"
    assert _entry_route({"segments": [{"p": 1}]}) == "canvas"
    assert _entry_route({}) == "standard"
    assert _image_route({"slideshow": True}) == "slideshow"
    assert _image_route({"gen_type": "text_image"}) == "text_done"
    assert _image_route({}) == "to_video"


# ---------------------------------------------------------------- 编译后图的真实边

def test_asset_fetch_node_exists():
    assert "asset_fetch" in compiled_graph.get_graph().nodes


def test_asset_fetch_wired_between_video_and_downstream():
    edges = _edges()
    assert ("video_generator", "asset_fetch") in edges
    assert ("asset_fetch", "qc_checker") in edges, "标准模式：asset_fetch → QC"
    assert ("asset_fetch", "synthesizer") in edges, "画布模式：asset_fetch → synthesizer"


def test_old_direct_edges_are_gone():
    """旧的 video_generator 直连必须消失，否则下载被跳过、QC 又回到死代码。"""
    edges = _edges()
    assert ("video_generator", "qc_checker") not in edges
    assert ("video_generator", "synthesizer") not in edges


def test_graph_compiled_without_checkpointer():
    """**锁定「不装 checkpointer」的决定**（批次 G）。

    MemorySaver 在本项目里提供**零功能、只提供无界内存泄漏**：
    - 全仓 grep `get_state`/`update_state`/`get_state_history` 零命中，
      恢复统一走 session_store 的 Redis 快照 + recovery.py
    - 实测跑 300 个 thread_id → saver.storage 累积 300 条且**永不清理**，
      每条含一份完整 state 副本
    - 粒度也不对：checkpointer 是「节点边界」，救不了 video_generator
      全段并发提交中途被杀的场景

    若将来确实需要（interrupt / human-in-the-loop / 时间旅行调试），
    请配**带 TTL 的持久化 saver**（如 PostgresSaver）并删除本测试 —— 不要退回内存版。
    """
    from app import graph

    assert graph.compiled_graph.checkpointer is None
    assert not hasattr(graph, "MemorySaver"), "MemorySaver 不该再被导入"


def test_synthesizer_and_qc_still_terminal_downstream():
    """回归护栏（A9 + B2 后更新）：QC 通过 → notify_final；QC 失败 → fix_looping
    → （retry | fix_give_up）→ 最终都汇到 notify_final。

    ⚠️ `qc_checker` 不再直连 END：终态回调统一由 notify_final 发出，
    否则回调会发生在 QC 之前，Java 任务提前转终态 → fix_looping 的产物被丢弃。
    """
    edges = _edges()
    assert ("synthesizer", "__end__") in edges
    assert ("qc_checker", "notify_final") in edges
    assert ("qc_checker", "fix_looping") in edges
    assert ("fix_give_up", "notify_final") in edges
    assert ("notify_final", "__end__") in edges


def test_fix_loop_actually_loops_back_to_video_generator():
    """B1/B2 的核心：fix_looping 必须能回到 video_generator 形成**真循环**。

    改之前 fix_looping 没有任何出边（LangGraph 视其为终端）——
    图能跑，但「修复重试」永远不会重试。这条断言锁住那个形态不再回归。
    同时 video_generator → asset_fetch 必须仍在，否则重生后不会再跑 QC。
    """
    edges = _edges()
    assert ("fix_looping", "video_generator") in edges, "没有回边 = 自愈循环不存在"
    assert ("fix_looping", "fix_give_up") in edges
    assert ("video_generator", "asset_fetch") in edges, "回环后必须重跑 QC"


def test_qc_never_reaches_end_directly():
    """A9 的核心约束：终态回调必须发生在 QC 之后。

    若 qc_checker 存在直达 END 的边，说明回调又回到了 QC 之前的位置。
    """
    edges = _edges()
    assert ("qc_checker", "__end__") not in edges


def test_notify_final_is_reachable_from_every_video_terminal():
    """防止某条路径既不发回调也不终止（任务卡 queued 只能等看门狗兜底）。"""
    g = compiled_graph.get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    # 视频链路的所有终端都必须能汇到 notify_final
    assert ("qc_checker", "notify_final") in edges or ("qc_checker", "fix_looping") in edges
    assert ("fix_give_up", "notify_final") in edges
    assert "notify_final" in g.nodes
    assert "fix_give_up" in g.nodes
