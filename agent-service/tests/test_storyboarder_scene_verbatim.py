"""分镜 `scene` 字段的场景锚定口径回归（2026-09-18）。

## 为什么钉这块

场景锚定图的 key 是 `analysis.scenes` 里的**场景描述原文**，而分镜的 `scene` 原先由模型
自由改写；旧提示词明写「尽量从已有场景参考里选，**或按其风格新造**」—— 这就是措辞漂移的源头。

真实 LLM 实测（同一个小说 + 同一份 analysis，各 3 次）：

    旧提示词：命中 12/36 = 33%，`scene` 与参考**整串逐字相同** 0/36
    新提示词：命中 24/36 = 67%，整串逐字相同 23/36 = 64%
    （取证脚本与逐次原始输出在 D:/cli_ai/hermes-temp/dw/，不入库）

命中率只有在「逐字相同」时才是确定的（前端 `anchors.ts` 先做整串包含，再退到分句覆盖率 ≥ 0.5），
所以这里钉住两件事：
1. 提示词里必须存在**逐字复制**的硬要求，且旧的漂移措辞不再出现；
2. `analysis.scenes` 的每一条都必须**逐字**出现在发给模型的 instructions 里
   （否则模型根本没有可复制的原文）。
"""
from __future__ import annotations

import pytest

from app.novel import storyboarder


def _squash(text: str) -> str:
    """去掉**所有**空白后再比短语。

    本轮给提示词加了 `scene_ref` 规则、重排了段落，结果「整串原样复制」被换行拆成
    「整串原样」+「复制」→ 三条断言一起挂掉。CJK 文案里换行只是排版，
    **规则是否还在**不该取决于它在第几列换行。所以短语断言两侧都过 `_squash`。
    （场景原文的**逐字**断言不走这个 —— 那种地方就是要一字不差。）
    """
    return "".join(text.split())


def test_prompt_demands_verbatim_scene_copy():
    """硬要求：属于已有场景参考之一 → 整串逐字复制；旧漂移措辞必须消失。"""
    t = storyboarder.SYSTEM_PROMPT_TEMPLATE

    # 旧措辞 = 漂移源头，必须删掉（它同时出现在字段说明与场景参考两处）
    assert _squash("或按其风格新造") not in _squash(t)

    # 逐字复制的硬要求（缺一不可的四条禁令）
    assert _squash("整串原样复制") in _squash(t)
    assert _squash("一个字都不许改") in _squash(t)
    assert _squash("不许合并两条") in _squash(t)
    assert _squash("不许只取其中几个分句") in _squash(t)

    # 只有确实不属于任何一条时才新造，且新造格式要求不丢
    assert _squash("没有任何对应") in _squash(t)
    assert _squash("才新造") in _squash(t)
    assert _squash("25-60 字") in _squash(t)
    assert _squash("地点 + 时间 + 天气/光线") in _squash(t)

    # 「像但不等」的近似改写被明确禁止（正是 0.43 覆盖率那类失败）
    assert _squash("禁止在已有条目上改几个字凑数") in _squash(t)

    # ★ 2026-09-18 新增：结构化绑定（分镜器给序号，代码取原文）
    assert _squash("scene_ref") in _squash(t)
    assert _squash("只填序号本身") in _squash(t)


def test_prompt_scene_rules_survive_formatting():
    """`.format()` 之后三条规则与场景原文都在（模板里的花括号不得被误用）。"""
    scenes = [
        "破败茅草屋，日间，茅顶局部焦黑坍塌，屋内狼藉散乱，灰烬与断木交错，"
        "阳光从破洞漏下，气氛凄凉无奈",
        "青翠小山坡，白日，微风拂过万木倾伏，阳光洒落草坡，少年躺坐其中，"
        "远处层峦叠嶂，氛围闲散中藏着窃喜",
    ]
    import json

    p = storyboarder.SYSTEM_PROMPT_TEMPLATE.format(
        target=6,
        characters_json=json.dumps({"陈浔": "二十岁青年"}, ensure_ascii=False),
        scenes_json=json.dumps(scenes, ensure_ascii=False),
        visual_style="水墨青蓝、暖黄侧光",
    )
    for s in scenes:
        assert s in p, "场景参考原文必须逐字进入提示词，模型才有可复制的原文"
    assert _squash("整串原样复制") in _squash(p)


class _FakeResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    """捕获 instructions 的假 agent —— 不打网络，只看发给模型什么。"""

    def __init__(self, sink: list):
        self.sink = sink

    async def run(self, prompt, instructions=None):
        self.sink.append((prompt, instructions))
        return _FakeResult([])


@pytest.mark.asyncio
async def test_scene_refs_are_sent_verbatim_to_model(monkeypatch):
    """`storyboard()` 必须把 `analysis.scenes` 逐条原样送进 instructions。"""
    sink: list = []
    monkeypatch.setattr(storyboarder, "_build_agent", lambda model: _FakeAgent(sink))
    scenes = ["破败茅草屋，日间，屋内狼藉散乱，阳光从破洞漏下，气氛凄凉无奈"]

    await storyboarder.storyboard(
        novel_text="测试原文",
        analysis={"characters": {}, "scenes": scenes, "visual_style": "写实"},
        target_segments=6,
        model=object(),
    )

    assert len(sink) == 1
    prompt, instructions = sink[0]
    assert prompt == "测试原文"
    assert instructions, "instructions 不能为空"
    assert scenes[0] in instructions
    assert _squash("整串原样复制") in _squash(instructions)
