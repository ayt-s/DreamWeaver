"""DreamWeaver MCP Server：把 DreamWeaver 的创作能力暴露成标准的 MCP 工具。

为什么要这一层（而不是直接让 Agent 调 HTTP）：
- MCP 是 AI 客户端（Cursor / Claude Desktop / 任意 MCP Host）的通用接入协议，接一次就能被所有 Host 复用；
- 工具能力、参数 schema、鉴权范围、调用审计集中在这一个进程里，不散落在各个客户端配置里；
- 两种传输共用同一套工具与策略：stdio（别人 clone 下来本地跑，零部署零成本）与
  streamable-http（带 Bearer Token，只给可信环境开）。

启动：
    python -m app.mcp                    # stdio，默认只读
    MCP_ALLOW_WRITE=1 python -m app.mcp  # stdio，放开生成类工具（本地自用）
    python -m app.mcp --transport http --port 8765   # streamable-http，必须带 token
"""
from __future__ import annotations

import argparse
import json
import time
from contextvars import ContextVar
from typing import Any, Callable, Optional

from mcp.server.mcpserver import MCPServer

from app.mcp import audit
from app.mcp.policy import READ, WRITE, TokenInfo, load_tokens, stdio_identity

_IDENTITY: ContextVar[Optional[TokenInfo]] = ContextVar("dreamweaver_mcp_identity", default=None)
_STDIO_IDENTITY: Optional[TokenInfo] = None  # main() 里按环境初始化

INSTRUCTIONS = """DreamWeaver 是 AI 短视频创作 Agent。可用工具分两类：
- 只读：查看生成任务、任务详情、画布内容与节点；查询 MCP 调用审计。
- 写入/计费：修改节点提示词、为画布图片节点生成首帧（按张计费，先 dry_run 看计划再执行）。
调用写入类工具前先向用户确认，生成类工具务必先 dry_run=True。"""


def _identity() -> TokenInfo:
    info = _IDENTITY.get()
    if info is not None:
        return info
    if _STDIO_IDENTITY is not None:
        return _STDIO_IDENTITY
    return TokenInfo(name="anonymous", scopes=frozenset())


def _guarded(tool: str, scope: str, args: dict, fn: Callable[[], Any]) -> dict:
    """统一的鉴权 + 审计 + 错误归一化入口。

    工具本身只关心业务，权限判定与审计不写进每个工具里（少一处漏配就少一个越权口子）。
    """
    info = _identity()
    started = time.perf_counter()
    if not info.allows(scope):
        duration = (time.perf_counter() - started) * 1000
        error = f"token「{info.name}」缺少 {scope} 权限，工具 {tool} 拒绝执行"
        audit.record(token=info.name, tool=tool, decision="denied", ok=False,
                     duration_ms=duration, args=args, error=error)
        return {"ok": False, "tool": tool, "error": error}
    try:
        data = fn()
    except Exception as exc:  # noqa: BLE001 — 工具失败要回给 Agent 而不是让进程挂掉
        duration = (time.perf_counter() - started) * 1000
        audit.record(token=info.name, tool=tool, decision="allowed", ok=False,
                     duration_ms=duration, args=args, error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}
    duration = (time.perf_counter() - started) * 1000
    audit.record(token=info.name, tool=tool, decision="allowed", ok=True,
                 duration_ms=duration, args=args)
    return {"ok": True, "tool": tool, "data": data}


def build_server() -> MCPServer:
    from app.agent import tools as dw  # 延迟导入：避免 --list-tools 时去读 settings

    server = MCPServer("dreamweaver", instructions=INSTRUCTIONS)

    @server.tool(description="列出最近的短视频生成任务（状态、错误信息、结果 URL）。只读。")
    def list_generation_tasks() -> dict:
        return _guarded("list_generation_tasks", READ, {}, dw.list_tasks)

    @server.tool(description="查看单个生成任务的状态、失败原因与产出 URL。只读。")
    def get_generation_task(task_id: int) -> dict:
        return _guarded("get_generation_task", READ, {"task_id": task_id},
                        lambda: dw.get_task(task_id))

    @server.tool(description="读取画布项目结构：节点、连线、版本号（分镜编排的入口）。只读。")
    def inspect_canvas(canvas_id: int) -> dict:
        return _guarded("inspect_canvas", READ, {"canvas_id": canvas_id},
                        lambda: dw.inspect_canvas(canvas_id))

    @server.tool(description="读取画布中单个节点的内容（提示词、图片、视频等）。只读。")
    def read_canvas_node(canvas_id: int, node_id: str) -> dict:
        return _guarded("read_canvas_node", READ, {"canvas_id": canvas_id, "node_id": node_id},
                        lambda: dw.read_node(canvas_id, node_id))

    @server.tool(description="修改画布节点提示词并保存。写操作，需要 write 权限。")
    def edit_canvas_prompt(canvas_id: int, node_id: str, new_prompt: str) -> dict:
        return _guarded("edit_canvas_prompt", WRITE,
                        {"canvas_id": canvas_id, "node_id": node_id, "new_prompt": new_prompt},
                        lambda: dw.edit_prompt(canvas_id, node_id, new_prompt))

    @server.tool(
        description="为画布上「有提示词但还没有图」的图片节点生成首帧。按张计费："
                    "先 dry_run=True 报告计划并征得用户同意，再 dry_run=False 执行。需要 write 权限。"
    )
    def generate_canvas_images(
        canvas_id: int,
        node_ids: Optional[list[str]] = None,
        count: int = 3,
        dry_run: bool = True,
        wait_seconds: int = 40,
    ) -> dict:
        return _guarded(
            "generate_canvas_images", WRITE,
            {"canvas_id": canvas_id, "node_ids": node_ids, "count": count,
             "dry_run": dry_run, "wait_seconds": wait_seconds},
            lambda: dw.generate_images(canvas_id, node_ids=node_ids, count=count,
                                       dry_run=dry_run, wait_seconds=wait_seconds),
        )

    @server.tool(description="最近的 MCP 调用审计记录（谁、调了什么、成没成功、耗时）。只读。")
    def recent_mcp_audit(limit: int = 20) -> dict:
        return _guarded("recent_mcp_audit", READ, {"limit": limit},
                        lambda: {"rows": audit.recent(limit), "stats": audit.stats()})

    return server


class BearerTokenMiddleware:
    """streamable-http 的 Bearer Token 鉴权（纯 ASGI，不依赖 Starlette 的中间件实现）。

    校验通过后把 TokenInfo 塞进 ContextVar，工具执行时同一请求链路上读得到；
    失败直接 401，不进入 MCP 传输层，也不消耗任何生成额度。
    """

    def __init__(self, app, tokens: dict[str, TokenInfo]):
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        raw = headers.get("authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
        info = self.tokens.get(token)
        if info is None:
            audit.record(token="(unknown)", tool="__auth__", decision="denied", ok=False,
                         duration_ms=0.0, error="invalid or missing Bearer token")
            body = json.dumps({"ok": False, "error": "invalid or missing Bearer token"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        reset = _IDENTITY.set(info)
        try:
            await self.app(scope, receive, send)
        finally:
            _IDENTITY.reset(reset)


def _print_tools() -> None:
    from app.mcp.tool_matrix import describe

    for name, scope, desc in describe():
        print(f"{name:<24} [{scope:<5}] {desc}")


def main(argv: list[str] | None = None) -> None:
    global _STDIO_IDENTITY

    parser = argparse.ArgumentParser(prog="python -m app.mcp", description="DreamWeaver MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--list-tools", action="store_true", help="打印工具与权限矩阵后退出")
    args = parser.parse_args(argv)

    if args.list_tools:
        _print_tools()
        return

    server = build_server()
    if args.transport == "stdio":
        _STDIO_IDENTITY = stdio_identity()
        server.run("stdio")
        return

    tokens = load_tokens()
    if not tokens:
        raise SystemExit(
            "HTTP 模式必须在 .mcp-tokens.json 里配置 token 白名单（参考 .mcp-tokens.example.json），"
            "没有任何 token 时服务不启动——空白的鉴权等于没有鉴权。"
        )
    import uvicorn

    app = BearerTokenMiddleware(server.streamable_http_app(host=args.host), tokens)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
