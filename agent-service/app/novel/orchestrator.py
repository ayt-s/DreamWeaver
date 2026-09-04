"""小说预处理编排器。

流程：splitter → analyzer → storyboarder → composer → 组装 camelCase 结果。
任何一步失败直接 raise，由路由层统一兜底成 500。
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.novel import analyzer, composer, splitter, storyboarder

logger = logging.getLogger(__name__)


def _build_default_model() -> Any:
    """复用 chat_agent 的模型创建方式：OpenAIChatModel + OpenAIProvider。"""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        api_key=settings.agnes_api_key,
        base_url=settings.agnes_base_url,
    )
    return OpenAIChatModel(settings.text_model, provider=provider)


async def preprocess_novel(
    novel_text: str,
    target_segments: int = 6,
    seconds_per_segment: int = 5,
    style: str = "电影写实",
    generate_character_portrait: bool = False,  # Phase 3: 调用 image_generator 出定妆图并注入每个图片节点
    model: Any = None,
) -> dict:
    """小说 → 分镜结构化产物。

    返回：
    {
      "novelSummary": str,
      "characters": dict[str, str],
      "scenes": list[str],
      "segments": list[dict],
      "totalSegments": int,
      "totalDurationSeconds": int,
    }
    """
    if not novel_text or not novel_text.strip():
        raise ValueError("novel_text 为空")

    model = model or _build_default_model()

    # 1) 切章（无 LLM）
    chapters = splitter.split_chapters(novel_text)
    if not chapters:
        raise ValueError("小说切章失败：空文本")

    # 2) 分析（LLM）
    analysis = await analyzer.analyze(novel_text, model=model)

    # 3) 分镜（LLM）
    raw_segments = await storyboarder.storyboard(
        novel_text=novel_text,
        analysis=analysis,
        target_segments=target_segments,
        model=model,
    )
    if not raw_segments:
        raise ValueError("分镜产出为空")

    # 4) 拼装 prompt（无 LLM），并 clamp 秒数到 [4, 12]
    for seg in raw_segments:
        seg["seconds"] = max(4, min(12, int(seg.get("seconds", seconds_per_segment))))
        seg["imagePrompt"] = composer.compose_image_prompt(seg, style, analysis)
        seg["videoPrompt"] = composer.compose_video_prompt(seg, style, analysis)
        # 补齐 id / chapter 兜底
        if not seg.get("id"):
            seg["id"] = f"s{len(raw_segments)}"

    total_duration = sum(int(s.get("seconds", 0)) for s in raw_segments)
    return {
        "novelSummary": analysis.get("summary", ""),
        "characters": analysis.get("characters", {}),
        "scenes": analysis.get("scenes", []),
        "segments": raw_segments,
        "totalSegments": len(raw_segments),
        "totalDurationSeconds": total_duration,
    }
