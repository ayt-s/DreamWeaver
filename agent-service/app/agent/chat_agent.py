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
读：
1. inspect_canvas(canvas_id) - 读取画布全部节点、连线和版本号
2. read_node(canvas_id, node_id) - 读取单个节点详情
3. list_tasks() / get_task(task_id) - 查生成任务（get_task 含失败原因，排障先用它）
改画布：
4. edit_prompt(canvas_id, node_id, new_prompt) - 改节点提示词（文本节点改 content，其余改 prompt）并立即落库
5. （没有整体保存画布的工具）—— 改内容一律走 edit_prompt，见下面「不做整份回写」
生成：
6. generate_images(canvas_id, node_ids, count, dry_run) - 给「有提示词还没图」的图片节点批量出首帧（文生图）
7. collect_images(canvas_id, task_ids) - 续收此前提交、还在生成中的出图任务
8. submit_task(gen_type, prompt, ...) - 提交单个生成任务（text_video / image_video / text_image）
9. concat_task(task_id) - 把任务的分段视频拼成一条成片（本地 ffmpeg，不消耗额度）

# 使用规范
- 用户在消息里会提供 canvas_id；不知道是哪个画布时，先问用户
- 改提示词：先 read_node 看现状，再 edit_prompt，并说明改了什么
- **不做整份回写**：不要试图把整份节点数组写回画布（画布 28 个节点 ≈ 12KB JSON，回显必然丢节点）。增删节点/连线/调整顺序请让用户在画布上操作（画布上：悬停节点右上角出现 × 即可删；选中节点后按 Delete 也可；顺序 = 节点从左到右，拖动节点即改顺序），你只负责改提示词
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
        _tools.get_task,
        _tools.submit_task,
        _tools.generate_images,
        _tools.collect_images,
        _tools.concat_task,
    ],
)
