"""工具与权限矩阵的单一来源。

server.py 注册工具时按这里声明的名字/权限挂载，--list-tools 也从这里打印，
两边不会漂：新增工具只改一处，漏配权限在启动时就暴露。
"""
from __future__ import annotations

from app.mcp.policy import READ, WRITE

# (工具名, 所需权限, 说明)
MATRIX: list[tuple[str, str, str]] = [
    ("list_generation_tasks", READ, "列出最近的短视频生成任务"),
    ("get_generation_task", READ, "查看单个任务的状态、失败原因与产出 URL"),
    ("inspect_canvas", READ, "读取画布结构：节点、连线、版本号"),
    ("read_canvas_node", READ, "读取画布中单个节点的提示词/图片/视频"),
    ("recent_mcp_audit", READ, "最近的 MCP 调用审计与统计"),
    ("edit_canvas_prompt", WRITE, "修改画布节点提示词（写）"),
    ("generate_canvas_images", WRITE, "为图片节点生成首帧，按张计费，支持 dry_run（写/计费）"),
]


def describe() -> list[tuple[str, str, str]]:
    return list(MATRIX)


def scope_of(tool: str) -> str:
    for name, scope, _ in MATRIX:
        if name == tool:
            return scope
    raise KeyError(f"未登记的工具：{tool}")
