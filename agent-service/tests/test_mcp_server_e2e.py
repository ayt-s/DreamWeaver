"""MCP Server 端到端测试：真起子进程 + 真走 stdio 传输 + 真调 Java API。

为什么不用 mock 打桩到底：
- 这一层的价值就在「传输 + 鉴权 + 审计 + 下游 HTTP」串起来是否通，
  mock 掉传输等于只测了自己写的 if；
- 用一个本地 stub HTTP 服务顶替 Java（task 列表），既能验证工具真的把下游数据透出来，
  又不依赖开发机上 Java 是否启动。

跑法：python -m pytest tests/test_mcp_server_e2e.py -q
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp.client.stdio import StdioServerParameters, stdio_client

pytest.importorskip("mcp.client.session", reason="需要 mcp>=2")
from mcp.client import ClientSession  # noqa: E402

SERVICE_ROOT = Path(__file__).resolve().parents[1]


class _StubJava(BaseHTTPRequestHandler):
    """最小 Java API stub：只回答 /api/tasks。"""

    def _send(self, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 — http.server 约定
        if self.path.startswith("/api/tasks"):
            self._send({"code": 0, "data": {"list": [
                {"id": 7, "status": "done", "prompt": "stub-任务-A"},
                {"id": 8, "status": "failed", "errorMessage": "stub-额度不足"},
            ]}})
        else:
            self._send({"code": 0, "data": {}})

    def log_message(self, *args):  # 静音
        pass


@pytest.fixture()
def stub_java():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubJava)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _text(result) -> dict:
    """把 CallToolResult 归一化成 dict：优先 structuredContent，其次解析 TextContent。"""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"结果里没有可解析内容：{result!r}")


@pytest.mark.asyncio
async def test_stdio_transport_auth_and_audit(tmp_path, stub_java):
    audit_db = tmp_path / "mcp_audit.db"
    env = {
        **os.environ,
        "JAVA_NOTIFY_URL": stub_java,
        "MCP_AUDIT_DB": str(audit_db),
        "MCP_ALLOW_WRITE": "",  # stdio 默认只读
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp"],
        env=env,
        cwd=str(SERVICE_ROOT),
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert names == {
                "list_generation_tasks", "get_generation_task", "inspect_canvas",
                "read_canvas_node", "recent_mcp_audit",
                "edit_canvas_prompt", "generate_canvas_images",
            }, f"工具清单漂了：{sorted(names)}"

            # 1) 只读工具：真的打到 stub Java 并把数据透出来
            listed = _text(await session.call_tool("list_generation_tasks", {}))
            assert listed["ok"] is True
            assert [t["id"] for t in listed["data"]["list"]] == [7, 8]

            # 2) 写工具：stdio 默认无 write 权限 → 拒绝，且不应打到下游
            denied = _text(await session.call_tool(
                "generate_canvas_images", {"canvas_id": 1, "dry_run": True}))
            assert denied["ok"] is False
            assert "write" in denied["error"]

            # 3) 审计工具能读到上面两次调用
            audit_view = _text(await session.call_tool("recent_mcp_audit", {"limit": 10}))
            assert audit_view["ok"] is True
            rows = audit_view["data"]["rows"]
            tools_seen = {(r["tool"], r["decision"], r["ok"]) for r in rows}
            assert ("list_generation_tasks", "allowed", True) in tools_seen
            assert ("generate_canvas_images", "denied", False) in tools_seen

    # 4) 审计真的落盘成了 SQLite 表（不是只活在内存里）
    with sqlite3.connect(audit_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM mcp_audit_log").fetchone()[0]
        denied_count = conn.execute(
            "SELECT COUNT(*) FROM mcp_audit_log WHERE decision='denied'").fetchone()[0]
    assert count >= 2
    assert denied_count == 1


@pytest.mark.asyncio
async def test_stdio_write_enabled_calls_downstream(tmp_path, stub_java):
    """MCP_ALLOW_WRITE=1 时写工具放行（这里用一个会失败的 canvas 调用验证它真的往下走了）。"""
    env = {
        **os.environ,
        "JAVA_NOTIFY_URL": stub_java,
        "MCP_AUDIT_DB": str(tmp_path / "audit2.db"),
        "MCP_ALLOW_WRITE": "1",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp"],
                                   env=env, cwd=str(SERVICE_ROOT))
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = _text(await session.call_tool("edit_canvas_prompt", {
                "canvas_id": 1, "node_id": "n1", "new_prompt": "x"}))
            # 不再是权限拒绝；是下游调用失败（stub 不提供画布接口 → 会被归一化成 ok=False + 具体异常）
            assert "write" not in (result.get("error") or "")
            with sqlite3.connect(env["MCP_AUDIT_DB"]) as conn:
                rows = conn.execute(
                    "SELECT decision FROM mcp_audit_log WHERE tool='edit_canvas_prompt'").fetchall()
            assert rows and rows[0][0] == "allowed"
