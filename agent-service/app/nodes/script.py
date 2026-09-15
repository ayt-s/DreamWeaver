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

SCRIPT_TEMPLATE_VERSION = "script_v1.2"

# v1.2（2026-09-15）：注入 plot_outline + 镜头多样化要求。
# 此前 Brief 只带主题/风格/时长/受众/情绪，用户给的剧情（小说章节等）在
# requirement_parser 那一步就被压没了 → 剧本节点只能自由发挥，
# 产出与原文无关（实测：给《长生烬》第一章，出的是「修士渡劫被烤鸡腿砸头」）。
SCRIPT_TEMPLATE = """
根据以下 Brief 创作短视频剧本：

Theme: {theme}
Style: {style}
Duration: {duration_seconds}秒
Audience: {audience}
Mood: {mood}
{plot_outline_line}{shot_count_line}

输出分镜列表（JSON 数组，总时长控制在 {duration_seconds} 秒内），每镜包含：
- shot_id: 镜头编号
- visual: 画面描述（主体+动作+场景）
- camera: 镜头运动（推/拉/摇/移/固定）+ 景别（远景/全景/中景/近景/特写）+ 机位（平视/俯拍/仰拍/航拍/过肩）
- duration: 该镜时长（秒，4~12 之间的整数）
- style_note: 风格提示（光照/色调/质感）
{camera_variety_line}{render_safety_line}
要求：各镜 duration 之和必须等于 {duration_seconds} 秒（不要多也不要少）。
{plot_outline_rule}只输出 JSON 数组，不要其他内容。
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
    # 幂等守卫（断点恢复）：已有非空 script → 跳过 LLM 创作，不重复花钱与耗时
    if state.get("script"):
        logger.info("script_writer 幂等跳过：已有 %d 镜剧本",
                    len(state.get("script") or []))
        return {}
    brief = state["brief"]
    total_seconds = state.get("total_seconds")
    shot_count = state.get("shot_count")
    # 显式镜头数：模板里强约束（LLM 爱自由发挥，必须点名）
    shot_count_line = ""
    if shot_count:
        shot_count_line = f"Shot count: {shot_count}（必须恰好 {shot_count} 个镜头，不多不少）"
    # 剧情主线：非空时注进模板并附强约束（人物/事件不得替换）
    # LLM 有时把多事件返回成数组（模板里写的是字符串），两种形式都要接住
    outline_raw = brief.get("plot_outline")
    if isinstance(outline_raw, list):
        outline = "；\n".join(str(x).strip() for x in outline_raw if str(x).strip())
    else:
        outline = str(outline_raw or "").strip()
    prompt = SCRIPT_TEMPLATE.format(
        theme=brief.get("theme", ""),
        style=brief.get("style", ""),
        duration_seconds=brief.get("duration_seconds", "5"),
        audience=brief.get("audience", ""),
        mood=brief.get("mood", ""),
        plot_outline_line=(
            f"剧情主线（严格按顺序展开这些关键事件）：\n{outline}\n" if outline else ""
        ),
        # 镜头同质化会让画面像复读（实测：4 镜全「中景+平视+缓推」画面很雷同）
        camera_variety_line=(
            "镜头语言要求：各镜的景别/机位/运镜要有变化（如远景交代环境、特写抓情绪、"
            "俯拍或跟拍制造动感），避免连续多镜重复同一组合。\n"
        ),
        # 画面可拍性：实测最常崩的两类镜头都出在「物件与人物身体贴合/悬浮」
        # （烤鸡腿悬在头顶、鸡骨与头皮融在一起）——分镜阶段就别写这种镜头
        render_safety_line=(
            "画面可拍性（重要）：只写视频模型能真实渲染的镜头——不要让物体悬浮在半空、"
            "附着或嵌进人物身体，不要让多物体重叠粘连，避免要求展示细小文字；"
            "夸张/荒诞的情节用可实现的载体表达（人物表情、肢体动作、道具掉落翻倒）。\n"
        ),
        plot_outline_rule=(
            "硬约束：人物、关键事件与结局必须与上面的剧情主线一致——可以补充画面细节与"
            "台词，但不得替换人物、不得改动事件、不得自创主线。\n" if outline else ""
        ),
        shot_count_line=shot_count_line,
    )
    raw = await _llm_json_with_retry(prompt, session_id=state["session_id"], temperature=0.3)
    script = parse_llm_json(raw)
    if not isinstance(script, list):
        raise ValueError(f"剧本输出格式错误: {str(script)[:200]}")
    # 时间轴确定性分配：给了总时长/镜头数就由后端算每镜秒数，
    # 不信任 LLM 的算术（它常算不平）。余数摊到前几镜，保证总和精确。
    if shot_count and isinstance(shot_count, int) and shot_count > 0:
        script = _normalize_shot_count(script, shot_count)
    total = total_seconds or _coerce_int(brief.get("duration_seconds"))
    if total and script:
        _distribute_duration(script, total)
    return {"script": script, "status": TaskStatus.SCRIPT_WRITING}


def _coerce_int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_shot_count(script: list, count: int) -> list:
    """把 LLM 产出的镜头数校正到用户指定值：多了截断，少了按最后镜复制补齐。"""
    if len(script) == count:
        return script
    if len(script) > count:
        return script[:count]
    # 少了：复制最后一镜补齐（保持结构合法，visual 会略有重复，比直接失败好）
    while len(script) < count:
        tail = dict(script[-1]) if script and isinstance(script[-1], dict) else {}
        tail["shot_id"] = len(script)
        script.append(tail)
    return script


def _distribute_duration(script: list, total: int) -> None:
    """把总时长按镜头数均分（余数摊到前面几镜），原地改写每镜 duration。

    例：total=30, n=4 → [8, 8, 7, 7]。总和精确等于 total。
    """
    n = len(script)
    if n == 0:
        return
    base = total // n
    remainder = total % n
    for i, shot in enumerate(script):
        if not isinstance(shot, dict):
            continue
        shot["duration"] = base + (1 if i < remainder else 0)