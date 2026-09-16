"""Pydantic AI Chat Agent：DreamWeaver 画布智能助手。

模型：agnes-2.5-flash（OpenAI 兼容）
工具：读画布 / 读节点 / 改 prompt / 保存画布 / 列任务
"""
from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.agent import tools as _tools
from app.config import settings


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
1. inspect_canvas(canvas_id) - 读取指定画布项目的所有节点和连线
2. read_node(canvas_id, node_id) - 读取单个节点的详细内容
3. edit_prompt(canvas_id, node_id, new_prompt) - 编辑节点提示词（保存到内存态）
4. save_canvas(canvas_id, nodes, edges) - 整体保存画布到数据库
5. list_tasks() - 列出最近的生成任务

# 使用规范
- 用户在消息里会提供 canvas_id；如果你不知道是哪个画布，先问用户
- 编辑 prompt 时：先 read_node 看现状，再 edit_prompt 给出新版，向用户说明改了什么
- 保存画布前：先 inspect_canvas 拿全量 nodes/edges，找到目标节点，改完后 save_canvas
- 生成任务时：不要真的提交视频生成任务（agent 侧只读不改生成），而是给出建议 prompt，让用户自己点生成
- 回答要具体：不要说"可以优化"，要给出具体的 prompt 文案，让用户一眼能看懂
- 用户是中文语境，用中文回答

# 输出风格
- 简洁、实用、可执行
- 涉及 prompt 编辑时，直接给出新的 prompt 全文，不要只给建议
- 涉及任务时，引用任务 id 和当前状态
"""


chat_agent: Agent = Agent(
    model=_build_chat_model(),
    system_prompt=SYSTEM_PROMPT,
    tools=[
        _tools.inspect_canvas,
        _tools.read_node,
        _tools.edit_prompt,
        _tools.save_canvas,
        _tools.list_tasks,
    ],
)
