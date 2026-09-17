"""小说预处理编排器。

流程：splitter → analyzer → storyboarder → composer → 组装 camelCase 结果。
任何一步失败直接 raise，由路由层统一兜底成 500。
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.novel import analyzer, composer, fidelity, splitter, storyboarder

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

    # 2.1) 视觉风格定稿：用户显式选了就用用户的；没选（空 = 自动）就用 AI 分析出的
    #      visual_style；两者都没有才回退默认值。
    #      此前 Java 侧硬编码"电影写实"，把 analyzer 的判断整个丢掉了——
    #      界面上显示的「视觉风格」其实不是 AI 识别的结果。
    effective_style = ((style or "").strip()
                       or str(analysis.get("visual_style") or "").strip()
                       or "电影写实")
    logger.info("预处理视觉风格: 入参=%r → 生效=%s", style, effective_style)

    # 3) 分镜（LLM）
    raw_segments = await storyboarder.storyboard(
        novel_text=novel_text,
        analysis=analysis,
        target_segments=target_segments,
        model=model,
    )
    if not raw_segments:
        raise ValueError("分镜产出为空")

    # 3.1) 忠实度校验（LLM，语义层 —— qc_checker 只做画面层，剧情偏差此前无人拦）。
    # 位置刻意在这里：分镜一旦确认就要转画布、开始烧视频额度，这是最省钱的拦截点。
    # 校验本身失败（LLM 抖动）绝不能拖垮预处理 → passed=None，只是没有结论。
    try:
        fidelity_report = await fidelity.check_fidelity(novel_text, raw_segments, model=model)
    except Exception as exc:
        logger.warning("分镜忠实度校验失败（不影响预处理）：%s", exc)
        fidelity_report = {"passed": None, "reason": "", "missing": [], "invented": [],
                           "error": str(exc)[:200]}

    # 3.2) 不通过 → 带审校意见重切一次；重切失败则保留第一版（分镜不能丢）
    if fidelity_report.get("passed") is False:
        logger.warning("分镜忠实度未通过：%s（带反馈重切一次）", fidelity_report.get("reason"))
        try:
            retried = await storyboarder.storyboard(
                novel_text=novel_text,
                analysis=analysis,
                target_segments=target_segments,
                model=model,
                rewrite_hint=fidelity.rewrite_hint(fidelity_report),
            )
        except Exception as exc:
            logger.warning("带反馈重切失败，保留第一版分镜：%s", exc)
            retried = []
        if retried:
            raw_segments = retried
            try:
                fidelity_report = {**await fidelity.check_fidelity(novel_text, raw_segments, model=model),
                                  "firstAttempt": fidelity_report, "attempts": 2}
            except Exception as exc:
                logger.warning("重切后复核失败，沿用第一次结论：%s", exc)
                fidelity_report = {**fidelity_report, "attempts": 2}
        else:
            fidelity_report = {**fidelity_report, "attempts": 1}
    else:
        fidelity_report = {**fidelity_report, "attempts": 1}

    # 4) 拼装 prompt（无 LLM），并 clamp 秒数到 [4, 12]
    for seg in raw_segments:
        seg["seconds"] = max(4, min(12, int(seg.get("seconds", seconds_per_segment))))
        seg["imagePrompt"] = composer.compose_image_prompt(seg, effective_style, analysis)
        seg["videoPrompt"] = composer.compose_video_prompt(seg, effective_style, analysis)
        # 补齐 id / chapter 兜底
        if not seg.get("id"):
            seg["id"] = f"s{len(raw_segments)}"
        # 结构化镜头字段转 camelCase（pydantic 字段名是 shot_size）：
        # Java NovelSegment 与前端画布节点的 cameraSpec 都按 camelCase 接
        if "shot_size" in seg:
            seg["shotSize"] = seg.pop("shot_size")

    total_duration = sum(int(s.get("seconds", 0)) for s in raw_segments)
    return {
        "novelSummary": analysis.get("summary", ""),
        "characters": analysis.get("characters", {}),
        # analyzer 的结构化判断：哪些角色是动物/灵兽（composer 靠它决定角色锚里放谁）。
        # 这里不带上，落库的 analysis_json 就会缺这个字段，后续读库重算只能退回关键词猜。
        "animalCharacters": analysis.get("animal_characters", []),
        "scenes": analysis.get("scenes", []),
        "segments": raw_segments,
        "totalSegments": len(raw_segments),
        "totalDurationSeconds": total_duration,
        # 实际生效的视觉风格（Java 侧落库 + 前端展示；空入参时这里就是 AI 分析结果）
        "visualStyle": effective_style,
        # 忠实度结论（Java 落进 analysis_json 的 fidelity 键；前端在转入画布前提示）
        "fidelity": fidelity_report,
        "fidelityWarning": fidelity.warning_text(fidelity_report),
    }
