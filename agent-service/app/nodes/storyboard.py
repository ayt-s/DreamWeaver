"""LangGraph 节点：storyboarder（分镜 → 英文提示词 + 生成参数）。

Phase 4 P0：mode 和 reference_images 初始为空，由后续 image_generator 节点回填。
"""
from app.config import settings
from app.gateway.agnes import gateway
from app.state import CreativeSessionState, TaskStatus

# Agnes 视频时长合法范围：4~12 秒（实测 API 返回 "seconds must be in [4, 12]"）
MIN_SECONDS = 4
MAX_SECONDS = 12

TRANSLATE_TEMPLATE = (
    "Translate the following Chinese video description to an English video-generation "
    "prompt. Output only the English prompt, no explanation.\n\n{text}"
)


async def translate_to_en(text: str) -> str:
    resp = await gateway.chat(
        TRANSLATE_TEMPLATE.format(text=text),
        model=settings.text_model,
        temperature=0.1,
    )
    return resp.strip()


async def storyboarder_node(state: CreativeSessionState) -> dict:
    # 用户上传的参考图（图生视频模式）：有则作为每镜参考图，空则后续 image_generator 自动生图回填
    user_ref_images = list(state.get("reference_images", []))
    storyboard = []
    for shot in state["script"]:
        cn_description = (
            f"{shot.get('visual', '')}，{shot.get('camera', '')}，{shot.get('style_note', '')}"
        )
        en_prompt = await translate_to_en(cn_description)
        # 时长钳制到 [4, 12]：分镜可能给 2-3s 短镜，但 Agnes 下限是 4s
        raw_seconds = int(shot.get("duration", 5))
        seconds = max(MIN_SECONDS, min(raw_seconds, MAX_SECONDS))
        # mode 和 reference_images 的填充规则：
        # - 用户传了参考图 → 用用户图，走 mode="reference"（agnès 参考模式）
        # - 否则留空，由 image_generator 节点自动生图回填
        storyboard.append({
            "shot_id": shot.get("shot_id", len(storyboard)),
            "prompt_en": en_prompt,
            "mode": "reference" if user_ref_images else "text",
            "seconds": str(seconds),
            "aspect_ratio": "16:9",
            "reference_images": list(user_ref_images),
            "cn_description": cn_description,
        })
    return {"storyboard": storyboard, "status": TaskStatus.STORYBOARD_WRITING}


async def canvas_storyboarder_node(state: CreativeSessionState) -> dict:
    """无限画布模式：用户自定片段列表 → storyboard（每段一镜，图生视频）。

    每个片段 = 一张参考图 + 一段视频内容描述（+ 可选时长），
    直接翻译为英文提示词，跳过剧本/分镜 LLM 生成环节——用户自己就是导演。
    """
    from app import events
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "canvas_storyboarder", "node_name": "画布分镜"})

    segments = list(state.get("segments", []))
    storyboard = []
    for idx, seg in enumerate(segments):
        image_url = str(seg.get("image_url", "")).strip()
        cn = str(seg.get("prompt", "")).strip()
        # 描述为空时给默认动作，避免空提示词
        if not cn:
            cn = "对图片内容做缓慢推进的动态运镜"
        en_prompt = await translate_to_en(cn)
        raw_seconds = int(seg.get("seconds", 5) or 5)
        seconds = max(MIN_SECONDS, min(raw_seconds, MAX_SECONDS))
        storyboard.append({
            "shot_id": idx,
            "prompt_en": en_prompt,
            "mode": "reference",  # 用户提供参考图，参考模式(agnès Video 2.5 合法值)
            "seconds": str(seconds),
            "aspect_ratio": "16:9",
            "reference_images": [image_url],
            "cn_description": cn,
        })

    await events.emit(state["session_id"], "node_completed",
                      {"node_id": "canvas_storyboarder", "summary": f"画布分镜 {len(storyboard)} 段"})
    return {"storyboard": storyboard, "status": TaskStatus.STORYBOARD_WRITING}
