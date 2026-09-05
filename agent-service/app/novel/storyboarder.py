"""小说分镜器（LLM）：把小说切成 4-12 秒一段的 storyboard segment。

用 pydantic_ai 的 structured output 保证返回 List[NovelSegmentPydantic]。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.utils.retry import with_retry


MIN_SECONDS = 4
MAX_SECONDS = 12


class NovelSegmentPydantic(BaseModel):
    """单个分镜片段。"""

    id: str = Field(..., description="s1 / s2 / …")
    chapter: int = Field(..., description="所属章节序号，从 1 开始")
    title: str = Field(..., description="5-10 字的小标题，如『山坡初遇』")
    plot: str = Field(..., description="2-3 句原文精简，50-120 字")
    characters: list[str] = Field(..., description="本段出现的角色名，0-3 个")
    scene: str = Field(..., description="具体场景：地点+时间+天气，如『江南小山村山坡，春日午后』")
    camera: str = Field(
        ...,
        description="专业镜头术语，如『广角长镜头缓慢横移』、『特写俯拍』、『推拉固定』",
    )
    seconds: int = Field(..., ge=1, le=99, description="4-12 秒的片段时长")
    mood: str = Field(..., description="2-6 字情绪，如『平静、坚韧』")

    @field_validator("seconds")
    @classmethod
    def _clamp_seconds(cls, v: int) -> int:
        return max(MIN_SECONDS, min(MAX_SECONDS, int(v)))


SYSTEM_PROMPT_TEMPLATE = """你是小说转漫剧分镜器。请根据输入的小说内容和已有分析，切出 {target} 个（不超过 {target} 个）4-12 秒的短片分镜。

硬性要求：
- 严格输出 JSON 数组，不要输出 JSON 以外的任何文字（不要 markdown 代码块、不要注释、不要解释）。
- 每个片段字段：id / chapter / title / plot / characters / scene / camera / seconds / mood。
- id 形如 s1、s2、…，按顺序编号，与数组下标 +1 一致。
- seconds 必须在 4-12 之间；宁可让片段偏多内容，也不要塞超过 12 秒。
- 内容不足 target 时，可以少于 target（不要硬凑空段）。
- title 5-10 字；plot 2-3 句、50-120 字（对原文做精简改写，不是照抄）。
- characters 是本段实际出现的角色名字列表（来自角色卡），0-3 个。
- scene 必须具体：地点 + 时间 + 天气，如『江南小山村山坡，春日午后』，不要写『室内』『户外』这类模糊词。
- camera 必须包含两部分：镜头类型 + 运动方向，如『广角横移』『中景跟拍』『特写固定』『俯拍推进』『全景推拉』。
  镜头类型可选：广角 / 中景 / 特写 / 全景 / 俯拍 / 仰拍
  运动方向可选：固定 / 横移 / 跟拍 / 推进 / 拉远 / 摇移 / 推拉
- mood 2-6 字情绪，如『平静、坚韧』。

时长分配规则（按情节密度）：
- 对话密集/动作戏/紧张情节 → 8-10 秒（给足时间展开）
- 静态描述/情绪铺垫/氛围镜头 → 5-6 秒
- 场景转场/过场镜头 → 4-5 秒
- 相邻片段时长差异不超过 3 秒，避免节奏抖动

角色卡（用于 characters 字段名保持与角色卡一致）：
{characters_json}

已有场景参考（scene 尽量从这些场景里选，或按其风格新造）：
{scenes_json}

整体视觉风格：{visual_style}
"""


def _build_agent(model: Any) -> Any:
    from pydantic_ai import Agent

    # output_type 是列表；pydantic_ai 会自动做 schema 校验
    return Agent(model=model, output_type=list[NovelSegmentPydantic])


@with_retry("LLM 分镜", preset="llm")
async def storyboard(
    novel_text: str,
    analysis: dict,
    target_segments: int,
    model: Any,
) -> list[dict]:
    """产出 segment 列表（camelCase 前已经是 snake，直接用 model_dump）。带重试：wifi 抖动时按 10/30/60s 退避。"""
    import json

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        target=target_segments,
        characters_json=json.dumps(analysis.get("characters", {}), ensure_ascii=False, indent=2),
        scenes_json=json.dumps(analysis.get("scenes", []), ensure_ascii=False),
        visual_style=analysis.get("visual_style", ""),
    )
    agent = _build_agent(model)
    # 用 user prompt 承载 system 指令 + 文本；pydantic_ai 支持在 run() 里覆盖 output_type 前的 system
    # 由于 Agent 构造时未指定 system_prompt，我们在 run() 里通过 instructions 参数传入
    result = await agent.run(
        novel_text[:6000],
        instructions=system_prompt,
    )
    return [seg.model_dump() for seg in result.output]
