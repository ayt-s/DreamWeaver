"""LangGraph 节点：requirement_parser（需求解析）。

输入 raw_prompt → 输出结构化 brief（JSON）。
Phase 1 先做「尽力解析」，interrupt 多轮澄清留 Phase 2。
"""
from app.config import settings
from app.gateway.agnes import gateway
from app.state import CreativeSessionState, TaskStatus
from app.utils.json_utils import parse_llm_json

BRIEF_TEMPLATE = """
用户需求：{prompt}

请解析为结构化 Brief，只输出 JSON，不要其他内容：
{{
  "theme": "主题（如产品宣传/品牌故事/知识科普）",
  "style": "风格（如科技感/温馨/商务）",
  "duration_seconds": "期望时长（4-12）",
  "audience": "目标受众",
  "mood": "情绪基调"
}}
"""


def validate_brief(raw: str) -> dict:
    data = parse_llm_json(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Brief 解析结果不是对象: {str(data)[:200]}")
    for key in ("theme", "style", "duration_seconds", "audience", "mood"):
        if key not in data:
            data[key] = ""
    return data


async def requirement_parser_node(state: CreativeSessionState) -> dict:
    prompt = BRIEF_TEMPLATE.format(prompt=state["raw_prompt"])
    raw = await gateway.chat(prompt, model=settings.text_model, temperature=0.1)
    brief = validate_brief(raw)
    return {"brief": brief, "status": TaskStatus.QUEUED}