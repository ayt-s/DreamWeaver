"""元素语义绑定的 `<Picture N>` 必须对照**这一段真实**的参考图数组来编号。

2026-09-17 修的真实缺陷：编号此前完全取自前端载荷、从不与实际数组核对 ——
而前端组装每段的 `reference_images` 是 `[本段自己的图, ...锚定图]`，
某段**没有自己的首帧图**时数组整体前移一位，于是
「"陈浔" refers to <Picture 2>」实际指向第 1 张 → **绑错对象**
（小说转画布后还没出首帧的段都会踩到）。

修法：`build_reference_bindings(bindings, ref_images)` 按 url 在**该段数组**里的位置现算；
这一段里没有这张图就跳过该绑定（顺带消灭「被 5 张上限截断后仍被悬空引用」）。
"""

import pytest

from app.nodes import storyboard as sb
from app.utils.prompting import build_reference_bindings

CHAR = "https://cdn.example.com/char.png"
FIRST = "https://cdn.example.com/first.png"


def test_index_is_computed_from_the_segments_own_array():
    bindings = [{"name": "陈浔", "imageUrl": CHAR}]

    role, _ = build_reference_bindings(bindings, [FIRST, CHAR])
    assert "<Picture 2>" in role[0], "有首帧图时锚定图排在第 2 位"

    role, _ = build_reference_bindings(bindings, [CHAR])
    assert "<Picture 1>" in role[0], "本段没有首帧图时编号必须跟着前移，否则绑错对象"


def test_binding_dropped_when_this_segment_lacks_the_url():
    """这一段里没有这张参考图 → 不产生悬空引用。"""
    role, keep = build_reference_bindings([{"name": "陈浔", "imageUrl": CHAR}], [FIRST])
    assert role == []
    assert keep == []


def test_falls_back_to_payload_index_when_no_url_given():
    """向后兼容：老前端只传编号时行为不变。"""
    role, _ = build_reference_bindings([{"name": "我", "imageIndex": 3}])
    assert "<Picture 3>" in role[0]


def test_mixed_payload_prefers_url():
    role, _ = build_reference_bindings(
        [{"name": "我", "imageIndex": 4, "imageUrl": CHAR}], [FIRST, CHAR],
    )
    assert "<Picture 2>" in role[0], "同时给了编号和 url 时以 url 现算为准"


@pytest.mark.asyncio
async def test_canvas_node_uses_per_segment_indices(monkeypatch):
    """★ 端到端护栏：画布模式下两段的编号可以不同。"""

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.events.emit", _noop)

    async def _fake_translate(_text: str) -> str:
        return "base prompt"

    monkeypatch.setattr(sb, "translate_to_en", _fake_translate)

    state = {
        "session_id": "t",
        "reference_bindings": [{"name": "陈浔", "imageUrl": CHAR}],
        "segments": [
            {"prompt": "陈浔走进山洞", "reference_images": [CHAR]},          # 没有自己的首帧
            {"prompt": "陈浔回头", "reference_images": [FIRST, CHAR]},       # 有自己的首帧
        ],
    }
    out = await sb.canvas_storyboarder_node(state)
    prompts = [s["prompt_en"] for s in out["storyboard"]]

    assert "<Picture 1>" in prompts[0], f"第 1 段编号错：{prompts[0]}"
    assert "<Picture 2>" in prompts[1], f"第 2 段编号错：{prompts[1]}"
    # 一致性要求句也要跟着来
    assert "exactly consistent" in prompts[0]
