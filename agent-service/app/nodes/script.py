"""LangGraph 节点：script_writer（剧本生成）。

brief → script（分镜列表）。模板版本号留痕（技能点：Prompt 模板版本化）。
"""
from app.config import settings
from app.gateway.agnes import gateway
from app.state import CreativeSessionState, TaskStatus
from app.utils.json_utils import parse_llm_json

SCRIPT_TEMPLATE_VERSION = "script_v1.0"

SCRIPT_TEMPLATE = """
根据以下 Brief 创作短视频剧本：

Theme: {theme}
Style: {style}
Duration: {duration_seconds}秒
Audience: {audience}
Mood: {mood}

输出分镜列表（JSON 数组，总时长控制在 {duration_seconds} 秒内），每镜包含：
- shot_id: 镜头编号
- visual: 画面描述（主体+动作+场景）
- camera: 镜头运动（推/拉/摇/移/固定）
- duration: 该镜时长（秒）
- style_note: 风格提示（光照/色调/质感）

只输出 JSON 数组，不要其他内容。
"""


async def script_writer_node(state: CreativeSessionState) -> dict:
    brief = state["brief"]
    prompt = SCRIPT_TEMPLATE.format(
        theme=brief.get("theme", ""),
        style=brief.get("style", ""),
        duration_seconds=brief.get("duration_seconds", "5"),
        audience=brief.get("audience", ""),
        mood=brief.get("mood", ""),
    )
    raw = await gateway.chat(prompt, model=settings.text_model, temperature=0.3)
    script = parse_llm_json(raw)
    if not isinstance(script, list):
        raise ValueError(f"剧本输出格式错误: {str(script)[:200]}")
    return {"script": script, "status": TaskStatus.SCRIPT_WRITING}