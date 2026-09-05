"""小说内容分析器（LLM）。

用 pydantic_ai 的 structured output 保证返回严格符合 schema 的 JSON，
pydantic_ai 内部会做重试，无需手写 JSON retry 循环。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.utils.retry import with_retry


class NovelAnalysis(BaseModel):
    """单章（或整本小说）分析结果。"""

    summary: str = Field(..., description="100-300 字的剧情梗概")
    characters: dict[str, str] = Field(
        ...,
        description="3-5 个主要角色，键为角色名，值为 15-40 字的特征卡（外貌/身份/性格）",
    )
    scenes: list[str] = Field(
        ...,
        description="3-5 个场景，具体到地点+时间+天气，如『江南小山村山坡，春日午后』",
    )
    props: list[str] = Field(
        default_factory=list,
        description="0-3 个关键道具，具体物件名，如『竹篓』、『油纸伞』",
    )
    tone: str = Field(..., description="1-2 句的叙事基调，如『冷峻而温暖』")
    visual_style: str = Field(
        ...,
        description="5-15 字的视觉风格短语，如『水墨青蓝、古木暖光、呼吸感长镜头』",
    )


SYSTEM_PROMPT = """你是小说内容分析器。请阅读输入的章节内容，抽取结构化信息。

硬性要求：
- 输出必须严格符合给定的 JSON schema，不要输出 JSON 以外的文字。
- characters 是 3-5 个主要角色，键是角色姓名，值是 15-40 字的特征卡（涵盖外貌、身份、性格或典型动作）。
- scenes 是 3-5 个场景，每个场景必须包含：地点 + 时间 + 天气，如『江南小山村山坡，春日午后』。
- props 是 0-3 个关键道具，具体物件名，可空。
- tone 是 1-2 句的叙事基调。
- visual_style 是 5-15 字的视觉风格短语，用逗号分隔若干风格词，如『水墨青蓝、古木暖光、呼吸感长镜头』。请根据文本内容自动提炼，不要套模板。
"""


def _build_agent(model: Any) -> Any:
    from pydantic_ai import Agent

    return Agent(model=model, system_prompt=SYSTEM_PROMPT, output_type=NovelAnalysis)


@with_retry("LLM 分析", preset="llm")
async def analyze(novel_text: str, model: Any) -> dict:
    """分析小说（传入前 8000 字），返回 camelCase dict。带重试：wifi 抖动时按 10/30/60s 退避。"""
    text_slice = novel_text[:8000]
    agent = _build_agent(model)
    result = await agent.run(text_slice)
    return result.output.model_dump()
