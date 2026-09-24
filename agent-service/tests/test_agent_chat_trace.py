"""画布助手（Pydantic AI agent）的出口与轨迹测试。

覆盖 2026-09-23 的三处修复：
1. `extract_tool_calls` 跨消息配对工具返回值 —— 旧实现只扫 `kind=="response"`，
   `result` 恒为 `{}`、`status` 恒为 `"called"`，前端看不出哪个工具失败；
2. `run_chat` 把整轮对话包成可序列化结果（LangSmith 埋点的输出）并带上资源上限；
3. `/v1/agent/enrich-prompt` 从「直连国际端点单点」改为走统一出口 `gateway.chat`。

一律不打真实网络：agent 用假 run，网关用假 chat。
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage

from app.agent import chat_agent as ca
from app.gateway.agnes import AgnesGateway
from app.main import app

client = TestClient(app)


def _msgs():
    """一轮含两个工具调用的真实形状消息：一个成功、一个被拒（重试提示）。"""
    return [
        ModelRequest(parts=[UserPromptPart(content="把第 3 个节点改一下")]),
        ModelResponse(parts=[
            ToolCallPart(tool_name="inspect_canvas", args={"canvas_id": 7}, tool_call_id="c1"),
            ToolCallPart(tool_name="delete_node", args={"canvas_id": 7, "node_id": "img9"},
                         tool_call_id="c2"),
        ]),
        ModelRequest(parts=[
            ToolReturnPart(tool_name="inspect_canvas", content={"node_count": 28},
                           tool_call_id="c1"),
            RetryPromptPart(content="节点 img9 不存在", tool_name="delete_node",
                            tool_call_id="c2"),
        ]),
        ModelResponse(parts=[TextPart(content="已按要求处理")]),
    ]


# === extract_tool_calls =====================================================

def test_跨消息配对出成功与失败两种状态():
    calls = ca.extract_tool_calls(_msgs())
    assert [c["tool_name"] for c in calls] == ["inspect_canvas", "delete_node"]
    assert calls[0]["status"] == "ok"
    assert '"node_count": 28' in calls[0]["result"]
    # 失败的工具必须能看出来 —— 这正是旧实现丢掉的信息
    assert calls[1]["status"] == "error"
    assert "节点 img9 不存在" in calls[1]["result"]


def test_没有返回值的调用标为called而不是假装成功():
    msgs = [
        ModelResponse(parts=[ToolCallPart(tool_name="concat_task", args={"task_id": 1},
                                          tool_call_id="cX")]),
    ]
    calls = ca.extract_tool_calls(msgs)
    assert calls[0]["status"] == "called"
    assert calls[0]["result"] == ""


def test_超长返回值截断并留标记():
    big = {"nodes": ["x" * 5000]}
    calls = ca.extract_tool_calls([
        ModelResponse(parts=[ToolCallPart(tool_name="inspect_canvas", args={},
                                          tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="inspect_canvas", content=big,
                                           tool_call_id="c1")]),
    ])
    assert calls[0]["truncated"] is True
    assert len(calls[0]["result"]) == ca._TOOL_RESULT_MAX_CHARS


def test_小返回值不截断():
    calls = ca.extract_tool_calls([
        ModelResponse(parts=[ToolCallPart(tool_name="list_tasks", args={}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="list_tasks", content={"total": 3},
                                           tool_call_id="c1")]),
    ])
    assert calls[0]["truncated"] is False


def test_字符串形态的args被解析成字典():
    """实测（2026-09-24）：真实一轮对话里 `args` 是 JSON 字符串，原实现只认 dict
    → 轨迹里 `inspect_canvas` 的参数显示成空的 `{}`，看不出 agent 传了哪个 canvas/node。"""
    calls = ca.extract_tool_calls([
        ModelResponse(parts=[ToolCallPart(tool_name="edit_prompt",
                                          args='{"canvas_id": 7, "node_id": "img3"}',
                                          tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="edit_prompt", content={"saved": True},
                                           tool_call_id="c1")]),
    ])
    assert calls[0]["args"] == {"canvas_id": 7, "node_id": "img3"}


def test_无法解析的args原样保留而不是丢掉():
    calls = ca.extract_tool_calls([
        ModelResponse(parts=[ToolCallPart(tool_name="edit_prompt", args="{坏掉的 json",
                                          tool_call_id="c1")]),
    ])
    assert calls[0]["args"] == {"_raw": "{坏掉的 json"}


# === run_chat ===============================================================

@pytest.mark.asyncio
async def test_一轮对话返回可序列化结果并默认带上限(monkeypatch):
    captured: dict = {}

    class _FakeResult:
        output = "已经改好"
        usage = RunUsage(requests=2, tool_calls=1, input_tokens=120, output_tokens=30)

        def all_messages(self):
            return _msgs()

    async def _fake_run(prompt, *, message_history=None, usage_limits=None):
        captured["prompt"] = prompt
        captured["limits"] = usage_limits
        captured["history"] = message_history
        return _FakeResult()

    monkeypatch.setattr(ca, "chat_agent",
                        SimpleNamespace(run=_fake_run, _model=SimpleNamespace(model_name="m")))

    import json

    out = await ca.run_chat("你好")
    json.dumps(out, ensure_ascii=False)  # 必须可序列化（LangSmith 输出 / HTTP 响应都用它）
    assert out["reply"] == "已经改好"
    assert out["usage"]["requests"] == 2
    assert out["tool_calls"][0]["status"] == "ok"
    # 不传 usage_limits 时用收紧密的那个，而不是 Pydantic AI 的默认 50
    assert captured["limits"] is ca.DEFAULT_USAGE_LIMITS
    assert ca.DEFAULT_USAGE_LIMITS.request_limit == 20


@pytest.mark.asyncio
async def test_带历史时轨迹只算本轮新增(monkeypatch):
    """实测（2026-09-24）：第 2 轮 `usage.tool_calls=0`（本轮没调工具），
    但 `all_messages()` 含历史 ⇒ 不切片就会把历史里的旧调用当本轮显示。"""
    prior = _msgs()
    new_turn = [
        ModelRequest(parts=[UserPromptPart(content="接着改")]),
        ModelResponse(parts=[TextPart(content="好")]),
    ]

    class _FakeResult:
        output = "好"
        usage = RunUsage(requests=1, tool_calls=0)

        def all_messages(self):
            return prior + new_turn

    async def _fake_run(prompt, *, message_history=None, usage_limits=None):
        return _FakeResult()

    monkeypatch.setattr(ca, "chat_agent",
                        SimpleNamespace(run=_fake_run, _model=SimpleNamespace(model_name="m")))

    out = await ca.run_chat("接着改", history=prior)
    assert out["tool_calls"] == []      # 本轮只有正文，没有工具调用
    assert out["usage"]["tool_calls"] == 0


# === 端点 ===================================================================

def test_对话端点透传工具轨迹与用量(monkeypatch):
    captured: dict = {}

    async def _fake_run_chat(prompt, *, history=None, usage_limits=None, sink=None):
        captured["prompt"] = prompt
        return {
            "reply": "改好了",
            "tool_calls": [{"tool_name": "edit_prompt", "args": {"node_id": "n1"},
                            "status": "ok", "result": '{"saved": true}', "truncated": False}],
            "usage": {"requests": 2, "tool_calls": 1, "input_tokens": 10, "output_tokens": 5},
            "model": "fallback:agnes-2.5-flash",
        }

    monkeypatch.setattr(ca, "run_chat", _fake_run_chat)

    body = client.post("/v1/agent/chat", json={
        "canvas_id": 7, "message": "把 n1 改一下",
        "history": [{"role": "user", "content": "先看下画布"},
                    {"role": "assistant", "content": "画布有 28 个节点"}],
    }).json()

    assert body["code"] == 0
    data = body["data"]
    assert data["reply"] == "改好了"
    assert data["canvas_id"] == 7
    assert data["usage"]["requests"] == 2
    assert data["tool_calls"][0]["status"] == "ok"
    assert data["tool_calls"][0]["truncated"] is False
    # 历史与画布上下文仍按原样拼进 prompt（本轮未改这块语义）
    assert "当前画布项目 id: 7" in captured["prompt"]
    assert "[历史-助手] 画布有 28 个节点" in captured["prompt"]


def test_对话端点空消息被拒():
    resp = client.post("/v1/agent/chat", json={"message": "   "})
    assert resp.status_code == 422


def test_对话端点把失败翻成中文并可重试(monkeypatch):
    async def _boom(prompt, *, usage_limits=None):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(ca, "run_chat", _boom)
    resp = client.post("/v1/agent/chat", json={"message": "你好"})
    assert resp.status_code == 500
    assert resp.json()["code"] != 0


def test_丰富提示词走统一网关(monkeypatch):
    captured: dict = {}

    async def _fake_chat(self, prompt, model=None, temperature=0.2, max_tokens=4096,
                         session_id=None):
        captured.update(prompt=prompt, model=model, temperature=temperature,
                        max_tokens=max_tokens)
        return "  一只橘猫坐在窗台上，逆光，浅景深  "

    monkeypatch.setattr(AgnesGateway, "chat", _fake_chat)

    body = client.post("/v1/agent/enrich-prompt",
                       json={"prompt": "一只猫", "gen_type": "text_image"}).json()
    assert body["data"]["prompt"] == "一只橘猫坐在窗台上，逆光，浅景深"
    # 系统提示与用户描述合并成一条消息发出去（gateway.chat 是单消息接口）
    assert "一只猫" in captured["prompt"]
    assert "文生图提示词" in captured["prompt"]
    assert captured["temperature"] == 0.7
    # 不传 max_tokens：额度给小会把 content 吃成空串（agnes 先吐 reasoning_content）
    assert captured["max_tokens"] == 4096


def test_丰富提示词空结果报错而不是返回空串(monkeypatch):
    async def _empty(self, prompt, model=None, temperature=0.2, max_tokens=4096,
                     session_id=None):
        return "   "

    monkeypatch.setattr(AgnesGateway, "chat", _empty)
    resp = client.post("/v1/agent/enrich-prompt",
                       json={"prompt": "一只猫", "gen_type": "text_image"})
    assert resp.status_code == 500
