"""画布助手多轮对话历史（2026-09-24 新增的真历史）。

三件事各自锁定：
1. `trim_messages` 必须**按轮**切 —— 按条数截断会留下悬空的 `ToolCallPart`（对应
   `ToolReturnPart` 被丢掉），下一轮请求带着非法序列发给模型；
2. 超长工具返回值（画布快照 12KB 量级）入库前截断并留提示，别把 4 轮快照全堆着烧 token；
3. 端点行为：有真历史时**不再**拼前端文本历史（否则模型看到重复内容），
   拿不到历史时优雅回落到旧路径（Redis 挂 = 退化，不是坏掉）。

一律不碰真实 Redis：store 用替身，或直接测纯函数。
"""
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from app.agent import chat_agent as ca
from app.agent import chat_store
from app.main import app

client = TestClient(app)


def _turn(n: int, tool_payload: object = None):
    """造一轮：用户提问 → 模型调工具 → 工具返回 → 模型给出正文回答。"""
    return [
        ModelRequest(parts=[UserPromptPart(content=f"第{n}问")]),
        ModelResponse(parts=[ToolCallPart(tool_name="inspect_canvas", args={"canvas_id": 7},
                                          tool_call_id=f"c{n}")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="inspect_canvas",
                                           content=tool_payload if tool_payload is not None
                                           else {"node_count": n},
                                           tool_call_id=f"c{n}")]),
        ModelResponse(parts=[TextPart(content=f"答{n}")]),
    ]


def _messages(turns: int, tool_payload: object = None):
    out = []
    for i in range(turns):
        out.extend(_turn(i, tool_payload))
    return out


# === 裁剪 ===================================================================

def test_只保留最后N轮且不留下悬空的工具调用():
    msgs = _messages(6)
    kept = chat_store.trim_messages(msgs, max_turns=2)

    # 每轮 4 条 ⇒ 2 轮 = 8 条
    assert len(kept) == 8
    calls = {p.tool_call_id for m in kept if m.kind == "response"
             for p in m.parts if isinstance(p, ToolCallPart)}
    returns = {p.tool_call_id for m in kept if m.kind == "request"
               for p in m.parts if isinstance(p, ToolReturnPart)}
    # 关键断言：被保留的每个工具调用都有对应返回（否则下一轮是非法序列）
    assert calls and calls <= returns


def test_轮次不够时不裁剪():
    msgs = _messages(2)
    assert len(chat_store.trim_messages(msgs, max_turns=4)) == len(msgs)


def test_超长工具返回值截断并留提示():
    big = {"nodes": ["x" * 9000]}
    kept = chat_store.trim_messages(_messages(1, tool_payload=big), max_turns=4)
    returned = [p for m in kept if m.kind == "request" for p in m.parts
                if isinstance(p, ToolReturnPart)][0]
    assert returned.content["_truncated"] is True
    assert "重新调用" in returned.content["_note"]
    assert len(returned.content["preview"]) == chat_store.TOOL_RESULT_LIMIT


def test_短工具返回值不动():
    kept = chat_store.trim_messages(_messages(1, tool_payload={"ok": 1}), max_turns=4)
    returned = [p for m in kept if m.kind == "request" for p in m.parts
                if isinstance(p, ToolReturnPart)][0]
    assert returned.content == {"ok": 1}


# === 存取（替身，不碰 Redis）=================================================

@pytest.mark.asyncio
async def test_存取往返走的是PydanticAI原生序列化(monkeypatch):
    saved: dict = {}

    class _FakeClient:
        async def get(self, key):
            return saved.get(key)

        async def set(self, key, value, ex=None):
            saved[key] = value

        async def delete(self, key):
            saved.pop(key, None)

    monkeypatch.setattr(chat_store.store, "enabled", True)
    monkeypatch.setattr(chat_store.store, "_get_client", lambda: _FakeClient())

    await chat_store.store.save("cid-1", _messages(1))
    loaded = await chat_store.store.load("cid-1")
    assert loaded is not None
    assert [m.kind for m in loaded] == [m.kind for m in _messages(1)]
    assert chat_store.trim_messages(loaded)  # 能再次处理（类型没坏）

    await chat_store.store.clear("cid-1")
    assert await chat_store.store.load("cid-1") is None


@pytest.mark.asyncio
async def test_坏数据当作新对话而不是500(monkeypatch):
    class _BadClient:
        async def get(self, key):
            return "{ 这不是合法的消息数组"

    monkeypatch.setattr(chat_store.store, "enabled", True)
    monkeypatch.setattr(chat_store.store, "_get_client", lambda: _BadClient())
    assert await chat_store.store.load("cid-bad") is None


# === 端点：真历史 vs 回落 ====================================================

def test_有真历史时不拼前端文本历史(monkeypatch):
    seen: dict = {}

    async def _fake_load(cid):
        return _messages(1)

    async def _fake_save(cid, messages):
        seen["saved_cid"], seen["saved_len"] = cid, len(messages)

    monkeypatch.setattr(chat_store.store, "load", _fake_load)
    monkeypatch.setattr(chat_store.store, "save", _fake_save)

    async def _fake_run_chat(prompt, *, history=None, usage_limits=None, sink=None):
        seen["prompt"], seen["history"] = prompt, history
        if sink is not None:
            sink["messages"] = _messages(2)
        return {"reply": "好", "tool_calls": [], "usage": {"requests": 1},
                "model": "m"}

    monkeypatch.setattr(ca, "run_chat", _fake_run_chat)

    body = client.post("/v1/agent/chat", json={
        "canvas_id": 7, "message": "接着改", "conversation_id": "cid-1",
        "history": [{"role": "user", "content": "上一轮的问题"}],
    }).json()

    assert body["data"]["history_source"] == "server"
    assert seen["history"] is not None
    # 不拼文本历史：模型不该看到重复内容
    assert "上一轮的问题" not in seen["prompt"]
    assert "用户消息：接着改" in seen["prompt"]
    assert seen["saved_cid"] == "cid-1"


def test_没有真历史时回落文本历史(monkeypatch):
    seen: dict = {}

    async def _no_history(cid):
        return None

    async def _fake_run_chat(prompt, *, history=None, usage_limits=None, sink=None):
        seen["prompt"], seen["history"] = prompt, history
        return {"reply": "好", "tool_calls": [], "usage": None, "model": "m"}

    monkeypatch.setattr(chat_store.store, "load", _no_history)
    monkeypatch.setattr(ca, "run_chat", _fake_run_chat)

    body = client.post("/v1/agent/chat", json={
        "canvas_id": 7, "message": "接着改", "conversation_id": "cid-none",
        "history": [{"role": "assistant", "content": "画布有 28 个节点"}],
    }).json()

    assert body["data"]["history_source"] == "client"
    assert seen["history"] is None
    assert "[历史-助手] 画布有 28 个节点" in seen["prompt"]


def test_不带conversation_id时不读也不写历史(monkeypatch):
    touched: list = []

    async def _load(cid):
        touched.append(("load", cid))
        return None

    async def _save(cid, messages):
        touched.append(("save", cid))

    monkeypatch.setattr(chat_store.store, "load", _load)
    monkeypatch.setattr(chat_store.store, "save", _save)

    async def _fake_run_chat(prompt, *, history=None, usage_limits=None, sink=None):
        if sink is not None:
            sink["messages"] = _messages(1)
        return {"reply": "好", "tool_calls": [], "usage": None, "model": "m"}

    monkeypatch.setattr(ca, "run_chat", _fake_run_chat)

    body = client.post("/v1/agent/chat", json={"message": "你好"}).json()
    assert body["data"]["conversation_id"] is None
    assert touched == []  # 无状态单轮：不碰历史存储


def test_清空对话会删服务端历史(monkeypatch):
    cleared: list = []

    async def _clear(cid):
        cleared.append(cid)

    monkeypatch.setattr(chat_store.store, "clear", _clear)
    body = client.request("DELETE", "/v1/agent/chat/cid-9").json()
    assert body["data"]["cleared"] is True
    assert cleared == ["cid-9"]
