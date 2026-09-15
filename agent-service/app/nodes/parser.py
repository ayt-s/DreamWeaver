"""LangGraph 节点：requirement_parser（需求解析）。

输入 raw_prompt → 输出结构化 brief（JSON）。
Phase 1 先做「尽力解析」，interrupt 多轮澄清留 Phase 2。
"""
import asyncio
import json
import logging
from app.config import settings
from app.gateway.agnes import gateway
from app.state import CreativeSessionState, TaskStatus
from app.utils.json_utils import parse_llm_json

logger = logging.getLogger(__name__)

BRIEF_TEMPLATE = """
用户需求：{prompt}

请解析为结构化 Brief，只输出 JSON，不要其他内容：
{{
  "theme": "主题（如产品宣传/品牌故事/知识科普）",
  "style": "风格（如科技感/温馨/商务）",
  "duration_seconds": "期望时长（4-12）",
  "audience": "目标受众",
  "mood": "情绪基调",
  "plot_outline": "剧情主线：按顺序列出用户需求里的 3~6 个关键事件（人物+做了什么+结果），每条一句话，必须忠实于用户原文；用户需求里没有具体剧情（如只给了「做个奶茶广告」这类主题）时填空字符串"
}}

⚠️ plot_outline 必须来自用户需求本身，**不得自行创作人物与事件**：
用户给了小说章节/故事梗概时，这里就是他那些人物和事件的浓缩，
丢了它下游剧本节点只能自由发挥，产出会与原文无关（2026-09-15 实测事故）。
"""


def validate_brief(raw: str) -> dict:
    data = parse_llm_json(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Brief 解析结果不是对象: {str(data)[:200]}")
    # plot_outline 缺失时补空串（下游剧本模板据此决定是否加剧情强约束）
    for key in ("theme", "style", "duration_seconds", "audience", "mood", "plot_outline"):
        if key not in data:
            data[key] = ""
    return data


async def _llm_json_with_retry(prompt: str, *, session_id: str | None = None,
                                model: str | None = None,
                                temperature: float = 0.1,
                                max_retries: int = 2) -> str:
    """调用 LLM 获取 JSON 响应，解析失败时重试。

    重试策略：3 次尝试，间隔 3s。每次重新调用 LLM（不是只重试解析，
    因为 LLM 可能每次都输出相同格式错误的文本）。
    """
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


async def requirement_parser_node(state: CreativeSessionState) -> dict:
    # 幂等守卫（断点恢复）：已有非空 brief → 跳过 LLM 解析，不重复花钱与耗时
    if state.get("brief"):
        logger.info("requirement_parser 幂等跳过：已有 brief，不重复调用 LLM")
        return {}
    from app import events
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "requirement_parser", "node_name": "需求解析"})
    prompt = BRIEF_TEMPLATE.format(prompt=state["raw_prompt"])
    raw = await _llm_json_with_retry(prompt, session_id=state["session_id"])
    brief = validate_brief(raw)
    # 时间轴精确控制：用户显式给了总时长/镜头数 → 覆盖 LLM 的自由估计
    # （LLM 常把「5秒」当默认值，用户手动设定必须优先）
    total_seconds = state.get("total_seconds")
    if total_seconds:
        brief["duration_seconds"] = str(total_seconds)
    shot_count = state.get("shot_count")
    if shot_count:
        brief["shot_count"] = str(shot_count)
    await events.emit(state["session_id"], "node_completed",
                      {"node_id": "requirement_parser", "summary": f"主题: {brief.get('theme', '')}"})
    return {"brief": brief, "status": TaskStatus.QUEUED}