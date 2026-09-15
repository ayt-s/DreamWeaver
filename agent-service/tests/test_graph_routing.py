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


def test_synthesizer_and_qc_still_terminal_downstream():
    """回归护栏：synthesizer → END，qc_checker 仍分叉到 END/fix_looping。"""
    edges = _edges()
    assert ("synthesizer", "__end__") in edges
    assert ("qc_checker", "__end__") in edges
    assert ("qc_checker", "fix_looping") in edges
