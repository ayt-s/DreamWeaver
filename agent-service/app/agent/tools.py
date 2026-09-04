"""Agent 工具：包装 Java API 为 Pydantic AI tool。

所有工具同步 HTTP 调用 Java（agent-service 进程内是 async，工具用 run_sync 包装）。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from app.config import settings


# Java Spring Boot 地址：默认同机 8080
JAVA_BASE_URL = getattr(settings, "java_notify_url", "") or "http://localhost:8080"


def _post(path: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    url = f"{JAVA_BASE_URL}{path}"
    resp = httpx.post(url, json=payload or {}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    # Java 统一响应 code/message/data
    if data.get("code") != 0:
        raise RuntimeError(f"Java API 返回错误 {path}: {data.get('message')}")
    return data.get("data") or {}


def _put(path: str, payload: dict | None = None, timeout: float = 15.0) -> dict:
    url = f"{JAVA_BASE_URL}{path}"
    resp = httpx.put(url, json=payload or {}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Java API 返回错误 {path}: {data.get('message')}")
    return data.get("data") or {}


def _get(path: str, timeout: float = 10.0) -> dict:
    url = f"{JAVA_BASE_URL}{path}"
    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Java API 返回错误 {path}: {data.get('message')}")
    return data.get("data") or {}


def inspect_canvas(canvas_id: int) -> dict:
    """读取指定画布项目的完整内容（节点 + 连线）。"""
    project = _get(f"/api/canvas/{canvas_id}")
    try:
        nodes = json.loads(project.get("nodesJson") or "[]")
    except json.JSONDecodeError:
        nodes = []
    try:
        edges = json.loads(project.get("edgesJson") or "[]")
    except json.JSONDecodeError:
        edges = []
    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def read_node(canvas_id: int, node_id: str) -> dict:
    """读取画布中单个节点的内容（含 prompt、图片、视频等）。"""
    project = inspect_canvas(canvas_id)
    for n in project.get("nodes", []):
        if n.get("id") == node_id:
            return {"id": node_id, "position": n.get("position"), "data": n.get("data", {})}
    return {"error": f"节点 {node_id} 不存在"}


def edit_prompt(canvas_id: int, node_id: str, new_prompt: str) -> dict:
    """编辑指定节点的 prompt 并立即保存回数据库。返回更新后的节点。"""
    if not new_prompt or not new_prompt.strip():
        return {"error": "new_prompt 不能为空"}
    project = inspect_canvas(canvas_id)
    nodes = project.get("nodes", [])
    edges = project.get("edges", [])
    target = None
    for n in nodes:
        if n.get("id") == node_id:
            target = n
            break
    if not target:
        return {"error": f"节点 {node_id} 不存在"}
    if "data" not in target:
        target["data"] = {}
    old_prompt = target["data"].get("prompt", "")
    target["data"]["prompt"] = new_prompt
    # 立即持久化到数据库
    _put(
        f"/api/canvas/{canvas_id}",
        {"nodesJson": json.dumps(nodes, ensure_ascii=False),
         "edgesJson": json.dumps(edges, ensure_ascii=False)},
    )
    return {
        "id": node_id,
        "node_type": target["data"].get("type"),
        "old_prompt": old_prompt[:200] + ("..." if len(old_prompt) > 200 else ""),
        "new_prompt": new_prompt[:200] + ("..." if len(new_prompt) > 200 else ""),
        "saved": True,
        "message": f"节点 {node_id} 的 prompt 已更新并保存到数据库",
    }


def save_canvas(canvas_id: int, nodes: list, edges: list) -> dict:
    """把画布节点/连线整体保存回数据库（用于 agent 编排后持久化）。"""
    return _put(
        f"/api/canvas/{canvas_id}",
        {"nodesJson": json.dumps(nodes, ensure_ascii=False), "edgesJson": json.dumps(edges, ensure_ascii=False)},
    )


def submit_task(gen_type: str, prompt: str, **extra: Any) -> dict:
    """提交一个生成任务到 Java 侧（Java 会转发到 agent-service）。

    gen_type: text_video / image_video / text_image
    extra: video_model, reference_images(逗号分隔 URL), segments(JSON 字符串) 等
    """
    payload: dict[str, Any] = {"genType": gen_type, "prompt": prompt, "userId": "agent-chat"}
    payload.update(extra)
    # 注意 Java CreateTaskRequest 用 camelCase
    return _post("/api/tasks/video", payload)


def list_tasks() -> dict:
    """列出最近的生成任务（含状态、错误消息、结果 URL）。

    Java 侧返回结构: {"data":{"list":[...]}}
    """
    data = _get("/api/tasks")
    # 兼容 {"list": [...]} 结构
    if isinstance(data, dict) and "list" in data:
        return data
    return {"list": data if isinstance(data, list) else []}
