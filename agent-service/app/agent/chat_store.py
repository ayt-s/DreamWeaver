"""画布助手的多轮对话历史（Redis，Pydantic AI 原生 message_history）。

## 为什么需要它

`/v1/agent/chat` 此前是**无状态**的：前端每轮把历史拍平成 `[历史-用户] …` 文本塞进
**一条** user message（`chat_api.py`）。后果是**工具调用与其返回值整段丢失**：

- agent 每轮都得重新 `inspect_canvas`（实测一轮 input_tokens ≈ 15k，画布 JSON 占大头）
- 上一轮提交的 `task_id`、画布版本号、冲突提示在下一轮都不存在，
  所以 system prompt 里才要写「不要重复提交」「保存被拒要重读」这类**补偿性规则**
- 角色没有结构（user/assistant 都是一段纯文本）⇒ 模型分不清「它自己说过的」与「用户说的」

现在按 `conversation_id` 存**结构化消息**（`ModelMessagesTypeAdapter` 往返），
前端只发本轮消息，后端把真历史交给 `agent.run(message_history=...)`。

## 硬约束（与 session_store 同款）

**Redis 不可用时全部静默降级**：`load()` 返回 `None`（= 从新对话开始），`save()` 变 no-op。
调用方在拿不到历史时会自动回落到「前端传来的文本历史」这条旧路径，所以 Redis 挂了
只是退化，不是坏掉。

## 键契约

    dw:agent:chat:{cid}     Pydantic AI 消息数组的 JSON，TTL 6h（可配）

TTL 比会话快照（24h）短：对话历史的价值集中在「刚才那几轮」，放久了既占内存又容易
让模型拿到过期的画布快照。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:  # 与 session_store 同一份依赖；缺包时整体降级
    import redis.asyncio as _aioredis
except Exception:  # pragma: no cover
    _aioredis = None
    logger.warning("redis 包不可用，画布助手多轮历史降级为无状态")

CHAT_KEY = "dw:agent:chat:{cid}"

#: 保留的**轮次数**（一轮 = 一条带 UserPromptPart 的 request 及其后续 response / 工具往返）。
#: 必须按轮切、不能按条数切：从中间截断会把某个 ToolCallPart 留着、对应
#: ToolReturnPart 丢掉，下一轮请求会带着「悬空的工具调用」发给模型。
MAX_TURNS = 4

#: 单条工具返回值的保存上限（字符）。画布快照一次就是 12KB 量级，
#: 4 轮原样堆着会白烧 1 万多 token —— 截断保 preview，并在提示里说明可重新调用工具。
TOOL_RESULT_LIMIT = 4000


class ChatHistoryStore:
    """按 conversation_id 存/取 Pydantic AI 消息数组。Redis 异常一律静默降级。"""

    def __init__(self) -> None:
        self._client: Any = None
        # 单测可置 False（避免污染真实 Redis、避免网络抖动影响断言）
        self.enabled = True

    def _get_client(self):
        if not self.enabled or _aioredis is None:
            return None
        if self._client is None:
            try:
                from app.config import settings

                # protocol=2：本机 Redis 3.2.100 不支持 HELLO（详见 session_store 的说明）
                self._client = _aioredis.from_url(
                    settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    protocol=2,
                )
            except Exception as exc:
                logger.debug("创建对话历史 Redis 客户端失败（降级为无状态）: %s", exc)
                return None
        return self._client

    def _ttl(self) -> int:
        try:
            from app.config import settings

            return max(300, int(getattr(settings, "chat_history_ttl_s", 21600)))
        except Exception:
            return 21600

    # ---------------------------------------------------------------- 读写

    async def load(self, cid: str) -> list | None:
        """读历史。无历史 / Redis 不可用 / 数据损坏 → `None`（调用方回落旧路径）。"""
        client = self._get_client()
        if client is None or not cid:
            return None
        try:
            raw = await client.get(CHAT_KEY.format(cid=cid))
        except Exception as exc:
            logger.debug("对话历史读取失败（降级）cid=%s: %s", cid, exc)
            return None
        if not raw:
            return None
        try:
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            messages = ModelMessagesTypeAdapter.validate_json(raw)
        except Exception as exc:
            # 反序列化失败 = 我们换了 Pydantic AI 版本、消息格式变了
            # ⚠️ 不能把坏数据继续喂回去（会 500），也不能让会话永久卡死 ⇒ 当作新对话
            logger.warning("对话历史反序列化失败，按新对话处理 cid=%s: %s", cid, exc)
            return None
        return list(messages)

    async def save(self, cid: str, messages: list) -> None:
        """写历史（覆盖）。裁剪 + 截断后入库，失败静默。"""
        client = self._get_client()
        if client is None or not cid or not messages:
            return
        try:
            from pydantic_ai.messages import ModelMessagesTypeAdapter

            trimmed = trim_messages(messages, MAX_TURNS)
            payload = ModelMessagesTypeAdapter.dump_json(trimmed).decode("utf-8")
            await client.set(CHAT_KEY.format(cid=cid), payload, ex=self._ttl())
        except Exception as exc:
            logger.debug("对话历史保存失败（降级）cid=%s: %s", cid, exc)

    async def clear(self, cid: str) -> None:
        """删掉一段对话（前端点「清空对话」时调用）。失败静默。"""
        client = self._get_client()
        if client is None or not cid:
            return
        try:
            await client.delete(CHAT_KEY.format(cid=cid))
        except Exception as exc:
            logger.debug("对话历史删除失败（降级）cid=%s: %s", cid, exc)


def trim_messages(messages: list, max_turns: int = MAX_TURNS) -> list:
    """只保留最后 `max_turns` 轮，并对超长工具返回值做截断。

    **按「轮」切而不是按条数切**：一轮 = 一条带 `UserPromptPart` 的 request + 它之后的
    response / 工具往返。按条数截断容易把 `ToolCallPart` 留下、对应的 `ToolReturnPart`
    丢掉 —— 那是发给模型的**非法序列**（悬空工具调用），会直接报错。
    """
    from pydantic_ai.messages import UserPromptPart

    starts = [
        i for i, m in enumerate(messages)
        if getattr(m, "kind", None) == "request"
        and any(isinstance(p, UserPromptPart) for p in (getattr(m, "parts", None) or []))
    ]
    kept = messages[starts[-max_turns]:] if len(starts) > max_turns else list(messages)
    return [_shrink_tool_returns(m) for m in kept]


def _shrink_tool_returns(message: Any) -> Any:
    """把单条工具返回值压到 `TOOL_RESULT_LIMIT` 以内（就地把 content 换成带提示的摘要）。

    改动的是**历史副本**，不影响本轮已回传给前端的轨迹。
    """
    from pydantic_ai.messages import ToolReturnPart

    parts = getattr(message, "parts", None)
    if not parts:
        return message
    for part in parts:
        if not isinstance(part, ToolReturnPart):
            continue
        try:
            text = part.content if isinstance(part.content, str) else json.dumps(
                part.content, ensure_ascii=False)
        except Exception:
            continue
        if len(text) <= TOOL_RESULT_LIMIT:
            continue
        part.content = {
            "_truncated": True,
            "_note": (
                "历史里的工具返回值已截断（完整内容可重新调用该工具获取；"
                "注意画布/任务状态可能已经被用户改动，需要最新数据时请重新调用）"
            ),
            "preview": text[:TOOL_RESULT_LIMIT],
        }
    return message


store = ChatHistoryStore()
