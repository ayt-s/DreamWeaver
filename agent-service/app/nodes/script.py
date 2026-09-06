"""LangGraph 节点：script_writer（剧本生成）。

brief → script（分镜列表）。模板版本号留痕（技能点：Prompt 模板版本化）。
LLM 输出解析失败时自动重试（最多 3 次），避免 LLM 偶发非 JSON 输出导致任务失败。
"""
import asyncio
import json
import logging

from app.config import settings
from app.gateway.agnes import gateway
from app.state import CreativeSessionState, TaskStatus
from app.utils.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

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


async def _llm_json_with_retry(prompt: str, *, session_id: str | None = None,
                                model: str | None = None,
                                temperature: float = 0.1,
                                max_retries: int = 2) -> str:
    """调用 LLM 获取 JSON 响应，解析失败时重试。"""
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            raw = await gateway.chat(prompt, model=model or settings.text_model,
                                      temperature=temperature, session_id=session_id)
            parse_llm_json(raw)  # 验证是合法 JSON
            return raw
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            logger.warning("LLM JSON 解析失败 (尝试 %d/%d): %s", attempt + 1,
                           max_retries + 1, str(e)[:100])
            if attempt < max_retries:
                await asyncio.sleep(3)
    raise ValueError(f"LLM 输出 {max_retries + 1} 次均非合法 JSON: {last_err}")


async def script_writer_node(state: CreativeSessionState) -> dict:
    brief = state["brief"]
    prompt = SCRIPT_TEMPLATE.format(
        theme=brief.get("theme", ""),
        style=brief.get("style", ""),
        duration_seconds=brief.get("duration_seconds", "5"),
        audience=brief.get("audience", ""),
        mood=brief.get("mood", ""),
    )
    raw = await _llm_json_with_retry(prompt, session_id=state["session_id"], temperature=0.3)
    script = parse_llm_json(raw)
    if not isinstance(script, list):
        raise ValueError(f"剧本输出格式错误: {str(script)[:200]}")
    return {"script": script, "status": TaskStatus.SCRIPT_WRITING}