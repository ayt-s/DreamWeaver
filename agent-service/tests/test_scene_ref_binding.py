"""场景参考的**结构化绑定**：分镜器只给序号，由代码取参考原文（2026-09-18）。

## 为什么要有这一层

上一轮把「同一场景必须整串逐字复制」写进提示词后，真机 A/B 命中率 33% → 67%，
但**整串逐字只有 64%**：剩下的是「像但不等」的变体（『破败茅草屋，日间…』写成
『破败茅草屋内，日间…』）。只要差一个字，前端 `anchors.ts` 的字面匹配就落空，
这一镜拿不到场景锚图 → 背景与全片对不上。

所以把「是哪一条」交给模型判断（受约束选择：输出一个整数），把**取原文**交给代码 ——
文本一致性因此变成确定性的。真机实验（索引路线逐字 6/6、无越界、无指错地点）见
技能 `references/round-2026-09-18-autofix-and-anchor-verbatim.md`。

## 锁定的行为

1. `resolve_scene` 只在序号合法（1..len）且条目非空时返回参考原文；**其余一律原样返回
   `seg["scene"]`**（老数据没有 `scene_ref`，行为与改动前逐字一致 —— 这是本文件的重点）；
2. `scene_ref` 的容错：字符串/浮点能收敛，垃圾值收敛成 0（**一个辅助字段不该把整次
   分镜生成打成 ValidationError**）；
3. 绑定必须落在 compose **之前**：`[场景]` 里最终是参考原文，且参考原文里点名的角色
   **不被裁出角色锚**（角色锚裁剪读的就是 `scene`）。
"""
from __future__ import annotations

import pytest

from app.novel import composer, orchestrator, storyboarder

SCENES = [
    "山村茅屋废墟，黄昏，余烬未熄，陈浔跪坐门外，神情呆滞绝望，氛围凄凉无助",
    "青翠小山坡，白日，微风拂过万木倾伏，阳光洒落草坡，氛围闲散中藏着窃喜",
]

BASE_SEG = {
    "id": "s1", "chapter": 1, "title": "废墟", "plot": "陈浔跪坐门外",
    "characters": ["陈浔"],
    "scene": "山村茅屋废墟，黄昏，余烬未熄",  # 模型自己的改写版（缺后半个分句）
    "camera": "中景固定，人物居中", "seconds": 5, "mood": "绝望",
}


# --------------------------------------------------------------- 1) resolve_scene


def test_resolve_scene_returns_reference_verbatim():
    """给了合法序号 → 逐字取参考原文（这才是场景锚的 key）。"""
    seg = {**BASE_SEG, "scene_ref": 1}
    assert composer.resolve_scene(seg, {"scenes": SCENES}) == SCENES[0]
    seg2 = {**BASE_SEG, "scene_ref": 2}
    assert composer.resolve_scene(seg2, {"scenes": SCENES}) == SCENES[1]


@pytest.mark.parametrize(
    "ref, analysis",
    [
        (0, {"scenes": SCENES}),          # 模型自己新造
        (3, {"scenes": SCENES}),          # 越界
        (-1, {"scenes": SCENES}),         # 负数
        ("x", {"scenes": SCENES}),        # 非数字
        (None, {"scenes": SCENES}),       # 缺失
        (1, {"scenes": []}),              # 没有场景参考
        (1, None),                        # 连 analysis 都没有
        (1, {"scenes": ["   "]}),         # 条目是空白
    ],
)
def test_resolve_scene_falls_back_to_model_text(ref, analysis):
    """ ★ 兜底：任何拿不到参考原文的情况都必须**原样返回模型写的 scene**。

    这是「老数据行为不变」的核心保证：没有 scene_ref 的存量分段走的就是这条路径。
    """
    seg = {**BASE_SEG, "scene_ref": ref}
    assert composer.resolve_scene(seg, analysis) == BASE_SEG["scene"]


def test_resolve_scene_without_field_keeps_legacy_behavior():
    """连字段都没有（存量数据）→ 与改动前逐字一致。"""
    assert composer.resolve_scene(dict(BASE_SEG), {"scenes": SCENES}) == BASE_SEG["scene"]


# ---------------------------------------------------- 2) scene_ref 字段的容错


@pytest.mark.parametrize(
    "raw, expect",
    [(2, 2), ("3", 3), (2.0, 2), (" 4 ", 4), ("第2条", 0), ("", 0), (None, 0), (-1, 0), (0, 0)],
)
def test_scene_ref_field_coerces_instead_of_raising(raw, expect):
    """ ★ 一个辅助字段不该有杀伤力：任何垃圾值都必须收敛成 0，而不是让整次生成挂掉。"""
    m = storyboarder.NovelSegmentPydantic(**{**BASE_SEG, "scene_ref": raw})
    assert m.scene_ref == expect


def test_scene_ref_missing_defaults_to_zero():
    m = storyboarder.NovelSegmentPydantic(**BASE_SEG)
    assert m.scene_ref == 0


# ---------------------------------------------------------- 3) 给模型的清单要带序号


def test_numbered_scenes_keep_order_and_index():
    """序号必须与 analysis.scenes 顺序**严格一致**（错位就会取错场景）。"""
    out = storyboarder._numbered_scenes(SCENES)
    lines = out.splitlines()
    assert lines[0].startswith("1. ") and SCENES[0] in lines[0]
    assert lines[1].startswith("2. ") and SCENES[1] in lines[1]


def test_numbered_scenes_empty_is_explicit():
    """没有场景参考时不能留空 —— 要明说「按规则 2 新造并把 scene_ref 填 0」。"""
    out = storyboarder._numbered_scenes([])
    assert "scene_ref" in out and "新造" in out


# ------------------------------------------- 4) 端到端：绑定发生在 compose 之前


@pytest.mark.asyncio
async def test_orchestrator_binds_scene_by_index_before_compose(monkeypatch):
    """ ★ 序号 → 代码取原文 → 进 [场景]；新造的那镜原样保留；点名的角色不被裁掉。"""
    analysis = {
        "summary": "s",
        "characters": {"陈浔": "少年陈浔，穿粗布短褂"},
        "scenes": SCENES,
        "visual_style": "3D 写实国漫",
        "props": [],
    }
    # 第 1 镜：模型改写了参考（老做法会漏匹配），但序号给对了 → 应当被**纠正**成原文
    # 第 2 镜：模型自己新造（序号 0）→ 必须原样保留它写的文本
    segs = [
        dict(BASE_SEG),
        {**BASE_SEG, "id": "s2", "chapter": 1, "title": "山洞",
         "scene": "山洞内部，深夜，篝火微燃", "scene_ref": 0,
         "camera": "全景横移，人物三分线", "mood": "平静"},
    ]
    segs[0]["scene_ref"] = 1

    async def fake_analyze(text, model=None):
        return analysis

    async def fake_storyboard(**kwargs):
        return [dict(s) for s in segs]

    async def fake_fidelity(text, segments, model=None):
        return {"passed": True, "reason": "", "missing": [], "invented": []}

    monkeypatch.setattr(orchestrator.analyzer, "analyze", fake_analyze)
    monkeypatch.setattr(orchestrator.storyboarder, "storyboard", fake_storyboard)
    monkeypatch.setattr(orchestrator.fidelity, "check_fidelity", fake_fidelity)

    out = await orchestrator.preprocess_novel("第一章 山坡初遇\n少年躺在山坡上。", model="stub")
    got = out["segments"]

    # 1) 命中序号 → scene 变成参考**原文**（逐字），并进 [场景]
    assert got[0]["scene"] == SCENES[0], "序号合法时必须用参考原文替换模型自己的改写"
    assert f"[场景] {SCENES[0]}" in got[0]["imagePrompt"]
    # 2) 序号 0 → 原样保留（别动模型新造的文本）
    assert got[1]["scene"] == "山洞内部，深夜，篝火微燃"
    # 3) 参考原文里点名了陈浔 → 角色锚不能被裁掉
    #    （裁剪读的是 seg["scene"]，所以绑定必须发生在 compose 之前）
    anchor_block = got[0]["imagePrompt"].split("；")[0]
    assert anchor_block.startswith("[角色锚]") and "陈浔" in anchor_block
