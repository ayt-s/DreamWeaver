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
        # - 用户传了参考图 → 用用户图，走 mode="image"
        # - 否则留空，由 image_generator 节点自动生图回填
        storyboard.append({
            "shot_id": shot.get("shot_id", len(storyboard)),
            "prompt_en": en_prompt,
            "mode": "image" if user_ref_images else "text",
            "seconds": str(seconds),
            "aspect_ratio": "16:9",
            "reference_images": list(user_ref_images),
            "cn_description": cn_description,
        })
    return {"storyboard": storyboard, "status": TaskStatus.STORYBOARD_WRITING}
