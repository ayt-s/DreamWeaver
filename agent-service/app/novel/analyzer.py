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
        description=(
            "3-5 个主要角色，键为角色名，值为 40-80 字的视觉特征卡。"
            "必须涵盖：年龄感、性别、体型、发色发型、面部特征（五官轮廓/肤色/眉眼神态）、"
            "标志性服饰/配饰、身份或职业暗示、典型性格动作。"
            "示例：『20 岁青年，男性，身形挺拔，黑色短发后梳，剑眉，肤色偏白，"
            "黑色道袍外罩青灰短褂，左手常年戴一只玉镯，举止沉稳内敛』。"
            "该特征卡将用于生成角色锚定图，必须视觉化，不要写抽象性格。"
        ),
    )
    scenes: list[str] = Field(
        ...,
        description=(
            "3-5 个场景，每个 25-60 字，必须包含："
            "地点 + 时间 + 天气/光线 + 主色调 + 关键视觉元素 + 氛围。"
            "示例：『江南小山村石屋客厅，白日，窗外阴雨朦胧，暖黄灯光，"
            "屋内木桌上摆着一盏油灯，气氛沉静克制』。"
            "禁止写『室内』『户外』这类模糊词，必须可视化。"
        ),
    )
    props: list[str] = Field(
        default_factory=list,
        description=(
            "0-3 个关键道具，具体物件名 + 视觉细节，"
            "如『竹编药篓（褐色，破了一个口）』『油纸伞（淡青色，伞面有墨点）』"
        ),
    )
    tone: str = Field(..., description="1-2 句的叙事基调，如『冷峻而温暖』")
    visual_style: str = Field(
        ...,
        description=(
            "5-20 字的视觉风格短语，用逗号分隔若干风格词。"
            "包含：色调 + 光影 + 镜头质感 + 时代/地域感。"
            "示例：『水墨青蓝、暖黄侧光、呼吸感长镜头、民国江南质感』"
        ),
    )


SYSTEM_PROMPT = """你是小说内容分析器。请阅读输入的章节内容，抽取结构化信息。

核心原则：
- 所有字段必须视觉化、可被渲染。禁止写抽象性格、纯文学修辞、无法用画面表现的概念。
- 优先级：角色视觉特征 > 场景视觉特征 > 其他。角色卡的每一个词都会直接影响锚定图的质量，宁多勿少。
- 特征卡描述必须具体到"能画出来"的程度：颜色、材质、形状、比例、位置。

硬性要求：
- 输出必须严格符合给定的 JSON schema，不要输出 JSON 以外的文字。
- characters 是 3-5 个主要角色，键是角色姓名，值是 40-80 字的视觉特征卡，
  必须涵盖：年龄感、性别、体型、发色发型、面部特征（五官轮廓/肤色/眉眼神态）、
  标志性服饰/配饰、身份或职业暗示。
- scenes 是 3-5 个场景，每个 25-60 字，必须包含：地点 + 时间 + 天气/光线 + 主色调 +
  关键视觉元素 + 氛围。禁止写"室内""户外"这类模糊词。
- props 是 0-3 个关键道具，具体物件名 + 视觉细节，可空。
- tone 是 1-2 句的叙事基调。
- visual_style 是 5-20 字的视觉风格短语，包含色调 + 光影 + 镜头质感 + 时代/地域感。
  请根据文本内容自动提炼，不要套模板。
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
