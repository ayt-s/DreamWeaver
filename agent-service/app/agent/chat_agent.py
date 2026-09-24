"""Pydantic AI Chat Agent：DreamWeaver 画布智能助手。

模型：agnes-2.5-flash（OpenAI 兼容）
工具：读画布 / 读节点 / 改 prompt / 保存画布 / 列任务
"""
from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.agent import tools as _tools
from app.config import settings
from app.utils.observability import traced


# Agnes 是 OpenAI 兼容端点
def _build_chat_model():
    """按端点池构造模型：intl 为主，cn 作 fallback。

    此前硬编码 settings.agnes_api_key/base_url（**只有国际端点**）：intl 报错或额度用尽时
    画布助手直接不可用，而国内 key 一直闲着。用 FallbackModel 让它在请求失败时自动切换，
    语义与同一页的「AI 生成」（走 gateway 轮询 cn/intl）对齐——都能用上国内端点。
    """
    models = [
        OpenAIChatModel(
            settings.text_model,
            provider=OpenAIProvider(api_key=p["api_key"], base_url=p["base_url"]),
        )
        for p in settings.agnes_providers
    ]
    if not models:  # 兜底：连 intl 都没配时保持旧行为，至少不在 import 期就崩
        models = [
            OpenAIChatModel(
                settings.text_model,
                provider=OpenAIProvider(
                    api_key=settings.agnes_api_key, base_url=settings.agnes_base_url
                ),
            )
        ]
    return models[0] if len(models) == 1 else FallbackModel(*models)


SYSTEM_PROMPT = """你是 DreamWeaver 画布智能助手，一个帮助用户在 AI 视频创作画布中完成编辑、优化和生成的 agent。

# 你拥有的工具
读：
1. inspect_canvas(canvas_id) - 读取画布全部节点、连线和版本号
2. read_node(canvas_id, node_id) - 读取单个节点详情
3. list_tasks() / get_task(task_id) - 查生成任务（get_task 含失败原因，排障先用它）
改画布：
4. edit_prompt(canvas_id, node_id, new_prompt) - 改节点提示词（文本节点改 content，其余改 prompt）并立即落库
5. add_image_node(canvas_id, prompt, after_node_id?) - 新增图片节点（分镜）并自动连到成片
6. delete_node(canvas_id, node_id) - 删除节点及它的连线（成片节点 compose 不可删）
7. connect_nodes(canvas_id, source, target) - 连一条线
8. reorder_shots(canvas_id, node_ids) - 按给定顺序重排分镜（成片顺序 = 图片节点从左到右）
生成：
6. generate_images(canvas_id, node_ids, count, dry_run) - 给「有提示词还没图」的图片节点批量出首帧（文生图）
7. collect_images(canvas_id, task_ids) - 续收此前提交、还在生成中的出图任务
8. submit_task(gen_type, prompt, ...) - 提交单个生成任务（text_video / image_video / text_image）
9. concat_task(task_id) - 把任务的分段视频拼成一条成片（本地 ffmpeg，不消耗额度）

# 使用规范
- 用户在消息里会提供 canvas_id；不知道是哪个画布时，先问用户
- 改提示词：先 read_node 看现状，再 edit_prompt，并说明改了什么
- **改结构用专用工具，绝不整份回写**：增删节点用 add_image_node / delete_node，连线用
  connect_nodes，调先后用 reorder_shots（node_ids 按你想要的顺序传）。
  画布 28 个节点 ≈ 12KB JSON，把整份数组回显回来必然丢节点，所以没有「整体保存」工具。
  你也可以告诉用户在画布上直接操作：悬停节点右上角的 × 删、选中后按 Delete、拖动即改顺序。
- 新加的图片节点是「待生成」状态：先提醒用户，用户同意后用 generate_images 出图才有画面上成片
- **出图必须先报计划**：generate_images 默认 dry_run=True，它会返回清单与总张数（按张计费）。
  把清单和总张数告诉用户，得到同意后才用 dry_run=False 执行；用户没明确同意就不要执行
- 已经提交过的生成不要重复提交（get_task / list_tasks 能查到）；还在生成中的用 collect_images 续收
- 保存被拒（返回 conflict=true）说明画布被同时改过：按返回的 message 重新 inspect_canvas 再决定，
  不要只向用户复述报错，也不要丢掉已生成的图（任务上的 URL 还在）
- 画布模式的任务在生成时已自动拼接成片；只有分段 ≥ 2、且没有成片时才需要 concat_task

# 输出风格
- 中文、简洁、可执行：直接给结论和下一步，不要罗列你调用了哪些工具
- **禁止 markdown 表格**（用户环境无法渲染 `| --- |`）：列表信息用无序列表逐条写
- 涉及提示词编辑时，直接给出新的提示词全文，不要只给建议
- 涉及生成时，说明预计张数/耗时，以及失败原因（引用任务 id）
"""


chat_agent: Agent = Agent(
    model=_build_chat_model(),
    system_prompt=SYSTEM_PROMPT,
    tools=[
        _tools.inspect_canvas,
        _tools.read_node,
        _tools.edit_prompt,
        _tools.list_tasks,
        _tools.add_image_node,
        _tools.delete_node,
        _tools.connect_nodes,
        _tools.reorder_shots,
        _tools.get_task,
        _tools.submit_task,
        _tools.generate_images,
        _tools.collect_images,
        _tools.concat_task,
    ],
)


# 单轮对话的资源上限。
#
# ⚠️ 不传 `usage_limits` 时 Pydantic AI 用的是**默认值** `UsageLimits(request_limit=50)`
# （见 pydantic_ai/usage.py:449）—— 不是无限，但也远超一轮画布助手对话的正常量
# （一轮工具调用 ≈ 2 次请求）。收紧到 20 次请求 / 20 次工具调用：
# 正常排障与批量出图够用，而模型万一在工具循环里打转时，代价被压在 20 轮内。
DEFAULT_USAGE_LIMITS = UsageLimits(request_limit=20, tool_calls_limit=20)

#: 工具返回值回传上限（字符）。`inspect_canvas` 一次能返回 12KB 的画布 JSON，
#: 原样塞进 HTTP 响应里会把响应体撑爆，而前端只展示前几行。
_TOOL_RESULT_MAX_CHARS = 400


def _shrink(value: Any) -> dict:
    """把工具返回值压成**可安全回传**的 dict（超长截断并显式标注）。

    ⚠️ 截断必须留标记：调用方（前端轨迹面板 / 排障）拿到的若是「看起来很短的完整结果」，
    会把截断误读成工具只返回了这么点东西。
    """
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value if value is not None else "")
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        return {"result": text[:_TOOL_RESULT_MAX_CHARS], "truncated": True}
    return {"result": text, "truncated": False}


def extract_tool_calls(messages: list) -> list[dict]:
    """从一轮对话的消息里提取工具调用轨迹（**跨消息配对**返回值）。

    ★ 2026-09-23 修：原实现（chat_api.py）只扫 `kind == "response"` 的消息，
    于是 `result` 恒为 `{}`、`status` 恒为 `"called"` —— 前端那个「工具调用」标签
    永远看不出哪个工具失败了。原因是配错了消息类型：

    - `ToolCallPart` 在 **response**（模型说要调什么）
    - `ToolReturnPart` / `RetryPromptPart` 在 **request**（我们把结果/报错喂回去），
      见 pydantic_ai/messages.py:2653 的 `ModelRequestPart` 联合类型

    所以必须两遍扫描、按 `tool_call_id` 配对，而不是在同一个 parts 列表里找。
    """
    outcomes: dict[str, tuple[str, Any]] = {}
    for msg in messages:
        if getattr(msg, "kind", None) != "request":
            continue
        for part in getattr(msg, "parts", []) or []:
            cid = getattr(part, "tool_call_id", None)
            if not cid:
                continue
            if isinstance(part, ToolReturnPart):
                # outcome 由 Pydantic AI 标记（success / failed）；缺失时按返回值处理
                outcome = getattr(part, "outcome", None) or "success"
                outcomes[cid] = ("ok" if outcome == "success" else "error", part.content)
            elif isinstance(part, RetryPromptPart):
                outcomes[cid] = ("error", part.content)

    calls: list[dict] = []
    for msg in messages:
        if getattr(msg, "kind", None) != "response":
            continue
        for part in getattr(msg, "parts", []) or []:
            if not isinstance(part, ToolCallPart):
                continue
            status, content = outcomes.get(part.tool_call_id or "", ("called", None))
            entry: dict = {"tool_name": part.tool_name, "args": _args_dict(part.args),
                           "status": status}
            entry.update(_shrink(content) if content is not None else
                         {"result": "", "truncated": False})
            calls.append(entry)
    return calls


def _args_dict(raw: Any) -> dict:
    """把 `ToolCallPart.args` 归一成 dict。

    ⚠️ 实测（2026-09-24）：`args` **不一定是 dict** —— Pydantic AI 允许它是
    **JSON 字符串**（流式/未解析形态）。原实现只认 `hasattr(args, "keys")`，
    于是真实一轮对话里 `inspect_canvas` 的参数在轨迹里是**空的 `{}`**
    （curl 实测：`"args":{}`，而工具确实收到了 canvas_id 并返回了 28 个节点）——
    排障时看不到 agent 到底把哪个 node_id 传下去了。
    """
    if hasattr(raw, "keys"):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
        return dict(parsed) if isinstance(parsed, dict) else {"_raw": text}
    return {}


@traced("agent.chat", run_type="chain")
async def run_chat(prompt: str, *,
                   history: list | None = None,
                   usage_limits: UsageLimits | None = None,
                   sink: dict | None = None) -> dict:
    """跑一轮画布助手对话，返回**纯可序列化**的结果。

    为什么单独包一层（2026-09-23 实测）：

    画布助手的 LLM 调用走 Pydantic AI（`OpenAIChatModel`），**不经 gateway**，
    而 LangSmith 的埋点只挂在 gateway 出口与图上 —— 实测同一分钟内
    `gateway.chat` 在 LangSmith 里有 run，`chat_agent.run` 一条都没有
    （`Agent._instrument_default` 默认 `False`，langsmith SDK 也没有 pydantic_ai 集成）。
    于是**用户最常触发的入口在 LangSmith 里完全不可见**。

    这里用 `traceable` 包住整轮对话（run_type=chain），把回复与工具轨迹一起上报；
    返回值刻意做成 dict（而不是 `AgentRunResult`）—— 只有可序列化的输出在
    LangSmith 里才读得懂。tracing 关闭时 `traced` 直接透传（见 utils/observability.py）。

    `history` 传 Pydantic AI 的结构化历史（来自 `chat_store`，含工具调用与返回值）；
    为 `None` 时退化成「只有本轮 prompt」——单轮语义与修复前一致。

    `sink` 用来把整轮消息**带出去**给调用方（写回 `chat_store`）：
    返回值必须保持可序列化，不能把 `all_messages()` 塞进 dict，所以走一个外部可变容器。
    """
    result = await chat_agent.run(
        prompt,
        message_history=history,
        usage_limits=usage_limits or DEFAULT_USAGE_LIMITS,
    )
    all_messages = list(result.all_messages())
    # ⚠️ 轨迹只算**本轮新增**的那部分：`all_messages()` = 历史 + 本轮，
    # 不切片的话前端在第二轮会把历史里的旧工具调用再显示一遍 ——
    # 实测第 2 轮 usage.tool_calls=0（本轮没调工具），轨迹里却挂着上一轮的
    # `inspect_canvas`，看起来像「它又读了一次画布」。
    new_messages = all_messages[len(history):] if history else all_messages
    if sink is not None:
        sink["messages"] = all_messages
    u = result.usage
    return {
        "reply": result.output,
        "tool_calls": extract_tool_calls(new_messages),
        "usage": {
            "requests": u.requests,
            "tool_calls": u.tool_calls,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
        },
        "model": getattr(chat_agent._model, "model_name", "unknown"),
    }
