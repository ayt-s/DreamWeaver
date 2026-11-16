# DreamWeaver MCP Server

把 DreamWeaver 的创作能力暴露成**标准 MCP 工具**，任何 MCP Host（Cursor、Claude Desktop、自研 Agent）接一次就能用。
代码在 `agent-service/app/mcp/`，与 FastAPI 主服务共用一个 Python 环境，但**独立进程**启动。

## 为什么单独做一层

| | 直接让 Agent 调 HTTP | 走 MCP Server |
| --- | --- | --- |
| 客户端接入 | 每个 Host 各写一套 HTTP 调用 | 接一次协议，所有 Host 通用 |
| 权限 | 散在客户端配置里（谁配谁有） | 集中到 Token + scope，服务端强制 |
| 审计 | 只能看网关日志 | 每次 tools/call 落审计表，含拒绝记录 |
| 成本控制 | 靠自觉 | 写入/计费类工具默认关闭，需显式授权 |

## 工具矩阵

| 工具 | 权限 | 说明 |
| --- | --- | --- |
| `list_generation_tasks` | read | 最近的生成任务（状态 / 错误 / 产出 URL） |
| `get_generation_task` | read | 单个任务详情，排障用 |
| `inspect_canvas` | read | 画布结构：节点、连线、版本号 |
| `read_canvas_node` | read | 单个节点的提示词 / 图片 / 视频 |
| `recent_mcp_audit` | read | 最近的 MCP 调用审计与按工具统计 |
| `edit_canvas_prompt` | write | 改节点提示词并保存 |
| `generate_canvas_images` | write | 为图片节点生成首帧，**按张计费**，支持 `dry_run` |

取值范围只有 `read` / `write`；`write` 隐含 `read`。工具清单与权限在 `app/mcp/tool_matrix.py` 里是单一来源，
`python -m app.mcp --list-tools` 打印的就是它，注册与打印不会漂。

## 两种传输

### 1. stdio（推荐给「别人 clone 下来自己跑」）

零部署、零端口、不暴露公网，宿主进程直接拉起服务：

```bash
cd agent-service
python -m app.mcp            # 默认只读
MCP_ALLOW_WRITE=1 python -m app.mcp   # 本地自用，放开生成类工具（按张计费，自己看着办）
```

MCP Host 侧配置（以 Claude Desktop / Cursor 的 `mcpServers` 为例）：

```json
{
  "mcpServers": {
    "dreamweaver": {
      "command": "D:/java/programe/DreamWeaver/agent-service/.venv/Scripts/python.exe",
      "args": ["-m", "app.mcp"],
      "cwd": "D:/java/programe/DreamWeaver/agent-service",
      "env": { "JAVA_NOTIFY_URL": "http://localhost:8080" }
    }
  }
}
```

> `JAVA_NOTIFY_URL` 指向已启动的 `web-backend`（默认 8080）。Java 没起时工具会返回 `ok=false` 和具体连接错误，
> 不会把 MCP 进程打挂。

### 2. streamable-http（给可信环境 / 远程演示）

```bash
cp agent-service/.mcp-tokens.example.json agent-service/.mcp-tokens.json   # 改掉里面的 token
python -m app.mcp --transport http --host 127.0.0.1 --port 8765
```

- 端点：`POST /mcp`，必须带 `Authorization: Bearer <token>`；无 token 或 token 不在白名单 → **401**，请求进不了传输层。
- 没有配置任何 token 时服务**拒绝启动**（空白鉴权等于没有鉴权）。
- Token 只存哈希前请自行评估：当前实现是明文比对，适合本地 / 内网演示；要上公网请在前面加 TLS 反代。

## 鉴权与审计

- `read` / `write` 两级 scope，按 Token 配置在 `.mcp-tokens.json`（已被 `.gitignore` 排除）。
- 每次 `tools/call`（含被拒绝的）写一行到 SQLite：`agent-service/data/mcp_audit.db`
  - 字段：`ts, token_name, tool, decision, ok, duration_ms, args_json, error`
  - 401 也会记一行 `tool=__auth__`，用于发现被扫的迹象
- 审计写失败不影响工具调用（宁丢日志不丢请求）。

查审计：

```bash
sqlite3 agent-service/data/mcp_audit.db \
  "select datetime(ts,'unixepoch','localtime'), token_name, tool, decision, ok, duration_ms from mcp_audit_log order by id desc limit 20;"
```

## 测试

```bash
cd agent-service
python -m pytest tests/test_mcp_server_e2e.py -q
```

端到端用例会真的拉起 `python -m app.mcp` 子进程、走 **真实 stdio 传输**、用本地 stub 顶替 Java，
覆盖：工具清单、只读工具透出下游数据、无 write 权限被拒、审计落盘、以及 `MCP_ALLOW_WRITE=1` 后写工具放行。

## 注意

- `generate_canvas_images` 会真的消耗图片生成额度，默认 `dry_run=True`，只有显式传 `dry_run=False` 才下单。
- 这一层不做业务逻辑，只做「协议适配 + 鉴权 + 审计」；业务仍由 `app/agent/tools.py` 与 Java 侧承担。
