"""LangGraph 节点：storyboarder（分镜 → 英文提示词 + 生成参数）。"""
from app.config import settings
from app.gateway.agnes import gateway
from app.state import CreativeSessionState, TaskStatus

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
    storyboard = []
    for shot in state["script"]:
        cn_description = (
            f"{shot.get('visual', '')}，{shot.get('camera', '')}，{shot.get('style_note', '')}"
        )
        en_prompt = await translate_to_en(cn_description)
        storyboard.append({
            "shot_id": shot.get("shot_id", len(storyboard)),
            "prompt_en": en_prompt,
            "mode": "text",
            "seconds": str(int(shot.get("duration", 4))),
            "aspect_ratio": "16:9",
            "reference_images": [],
            "cn_description": cn_description,
        })
    return {"storyboard": storyboard, "status": TaskStatus.STORYBOARD_WRITING}