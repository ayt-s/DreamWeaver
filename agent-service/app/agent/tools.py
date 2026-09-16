"""Agent 工具：包装 Java API 为 Pydantic AI tool。

所有工具同步 HTTP 调用 Java（agent-service 进程内是 async，工具用 run_sync 包装）。
"""
from __future__ import annotations

import json
import time
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


def _parse_nodes_json(nodes_json: str) -> tuple[list, dict | None]:
    """解析 nodesJson：兼容纯数组（历史数据）与 {theme, nodes} 包装（前端 2026-09 后格式）。

    返回 (nodes, wrapper)；wrapper 非 None 表示原数据带 theme 包装，保存时需保留。
    """
    try:
        parsed = json.loads(nodes_json or "[]")
    except json.JSONDecodeError:
        return [], None
    if isinstance(parsed, dict):
        return parsed.get("nodes") or [], parsed
    return parsed, None


def _serialize_nodes(nodes: list, wrapper: dict | None) -> str:
    """按原格式序列化 nodes——带包装则保留 theme，避免 agent 保存后主题丢失。"""
    if wrapper is not None:
        wrapped = dict(wrapper)
        wrapped["nodes"] = nodes
        return json.dumps(wrapped, ensure_ascii=False)
    return json.dumps(nodes, ensure_ascii=False)


def _parse_urls(raw: Any) -> list[str]:
    """解析 Java 侧 URL 字段（JSON 字符串数组）。"""
    if isinstance(raw, list):
        return [str(u) for u in raw if u]
    try:
        parsed = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(u) for u in parsed if u] if isinstance(parsed, list) else []


def _normalize_prompt(s: str) -> str:
    """归一化提示词：剥掉 [角色锚]/[镜头] 等方括号块并压缩空白。

    精准匹配只在「用户一个字都没动」时成立；改个标点整段就变了，图其实还在库里。
    """
    import re
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", " ", s or "")).strip()


# === 画布读写（乐观锁） ====================================================
# 背景：画布加乐观锁后，expectedVersion 为 null 会退化成「无条件写」。
# 助手若不带 version，就会成为唯一一处「用户有保护、agent 没有」的静默覆盖路径
# （用户同时编辑 → 助手保存 → 用户的改动无声消失）。所以这里的读-改-写
# 必须在**同一次工具调用内**完成：version 由工具自己读取并回传，
# 不能指望 LLM 把 version 从 inspect_canvas 一路透传到 save_canvas。
# 锁窗口从「整段对话」（分钟级）压到一次 HTTP 往返（毫秒级）。


def _get_canvas(canvas_id: int) -> dict:
    """读取画布原始数据（nodes/edges/version），工具内共用同一份快照。"""
    project = _get(f"/api/canvas/{canvas_id}")
    nodes, wrapper = _parse_nodes_json(project.get("nodesJson"))
    try:
        edges = json.loads(project.get("edgesJson") or "[]")
    except (json.JSONDecodeError, TypeError):
        edges = []
    return {
        "project": project,
        "nodes": nodes if isinstance(nodes, list) else [],
        "edges": edges if isinstance(edges, list) else [],
        "wrapper": wrapper,
        "version": project.get("version"),
    }


def _save_canvas(canvas_id: int, nodes: list, edges: list,
                 wrapper: dict | None, version: Any) -> dict:
    """带乐观锁保存，返回 Java 的 SaveCanvasResult（conflict=true 表示未写入）。"""
    payload: dict[str, Any] = {
        "nodesJson": _serialize_nodes(nodes, wrapper),
        "edgesJson": json.dumps(edges, ensure_ascii=False),
    }
    if version is not None:
        payload["version"] = version
    return _put(f"/api/canvas/{canvas_id}", payload)


def _conflict(canvas_id: int, res: dict, action: str, expected: Any) -> dict:
    """把版本冲突翻译成 LLM 能自己纠正的结果。

    不抛异常：抛了 LLM 只会向用户复述报错。返回结构化信息，它才知道下一步
    该 inspect_canvas 重读、并判断这一步是否还需要做。
    """
    server_nodes, _ = _parse_nodes_json(res.get("serverNodesJson"))
    return {
        "saved": False,
        "conflict": True,
        "action": action,
        "server_version": res.get("serverVersion"),
        "server_node_count": len(server_nodes) if isinstance(server_nodes, list) else None,
        "message": (
            f"保存被拒绝：画布 {canvas_id} 在你读取（版本 {expected}）之后被修改过"
            f"（服务端版本 {res.get('serverVersion')}）。为避免覆盖对方的改动，本次「{action}」未写入。"
            "请重新调用 inspect_canvas 读取最新内容，判断这一步是否还需要做，再重试。"
        ),
    }


def inspect_canvas(canvas_id: int) -> dict:
    """读取指定画布项目的完整内容（节点 + 连线 + 版本号）。"""
    snap = _get_canvas(canvas_id)
    nodes, edges = snap["nodes"], snap["edges"]
    return {
        "id": snap["project"].get("id"),
        "name": snap["project"].get("name"),
        "version": snap["version"],
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
    """编辑指定节点的 prompt 并立即保存回数据库。返回更新后的节点。

    字段名按节点类型定：文本节点的正文在 content，图片/视频节点的提示词在 prompt。
    写错字段的后果是「助手说改好了，节点上什么都没变」。
    """
    if not new_prompt or not new_prompt.strip():
        return {"error": "new_prompt 不能为空"}
    snap = _get_canvas(canvas_id)
    nodes, edges = snap["nodes"], snap["edges"]
    target = None
    for n in nodes:
        if n.get("id") == node_id:
            target = n
            break
    if not target:
        return {"error": f"节点 {node_id} 不存在"}
    if "data" not in target or not isinstance(target["data"], dict):
        target["data"] = {}
    field = "content" if target.get("type") == "textNode" else "prompt"
    old_prompt = target["data"].get(field, "") or ""
    target["data"][field] = new_prompt
    res = _save_canvas(canvas_id, nodes, edges, snap["wrapper"], snap["version"])
    if res.get("conflict"):
        return _conflict(canvas_id, res, f"对节点 {node_id} 的修改", snap["version"])
    canvas = res.get("canvas") or {}
    return {
        "id": node_id,
        "node_type": target.get("type"),
        "field": field,
        "old_prompt": old_prompt[:200] + ("..." if len(old_prompt) > 200 else ""),
        "new_prompt": new_prompt[:200] + ("..." if len(new_prompt) > 200 else ""),
        "saved": True,
        "version": canvas.get("version"),
        "message": f"节点 {node_id} 的 {field} 已更新并保存到数据库",
    }


# 注：这里原有一个 save_canvas(canvas_id, nodes, edges) —— 已移除。
# 它要求 LLM 把整份 nodes 数组回显回来，而实测画布 7 是 12,802 字符 / 28 节点：
# 模型漏一个节点或中途截断，就是「成功写入一份残缺画布」——乐观锁只保证版本对，
# 不保证内容对。助手改内容走 edit_prompt（服务端定点改），改结构由用户操作。


# === 生成类工具 ============================================================
# 助手以前只能「读 + 改提示词」，生成一律回「你自己点一下」——而它恰恰是唯一能听懂
# 自然语言意图的入口（「这几镜都给我出图」「拼成一条」）。以下工具把 Click 变成 Tool Call。
# 成本红线：生成按张计费，所以 generate_images 默认 dry_run=True —— 先报计划、
# 拿到用户同意才允许真提交。

AGENT_USER_ID_NOTE = (
    "不传 userId：Java 侧 CreateTaskRequest.userId 只接受数字（@Pattern + Long.valueOf），"
    "而前端 createVideoTask 从来不带该字段（落库为 null）。助手若自造 \"agent-chat\" 会被 400 拒绝，"
    "所以这里与前端保持一致 —— 提交出的任务归游客，配额口径与用户自己点的完全一致。"
)
# 前端单次请求 120s 超时；工具内等待必须留足 LLM 与网络的时间，硬上限 60s。
MAX_WAIT_SECONDS = 60
# 单次工具调用允许提交的最大张数（**成本闸门放在代码里，不指望 LLM 自觉**）。
# 实测画布 7 有 28 个节点；若助手在一次调用里对 25 个待生成节点各出 5 张 = 125 张，
# 那是几百块钱的一次点击。超限直接拒绝并要求缩小范围（传 node_ids 或降 count）。
MAX_IMAGES_PER_CALL = 12


def get_task(task_id: int) -> dict:
    """查单个任务的状态与失败原因（排障用，比 list_tasks 更聚焦）。"""
    t = _get(f"/api/tasks/{task_id}")
    t = t if isinstance(t, dict) else {}
    prompt = t.get("prompt") or ""
    return {
        "task_id": t.get("id", task_id),
        "gen_type": t.get("genType") or t.get("gen_type"),
        "status": t.get("status"),
        "error_message": t.get("errorMessage") or t.get("error_message"),
        "prompt": prompt[:120] + ("..." if len(prompt) > 120 else ""),
        # 明确标注截断：否则调用方会拿它当完整 prompt 去匹配画布节点（我自己踩过）
        "prompt_truncated": len(prompt) > 120,
        "image_urls": _parse_urls(t.get("imageUrls") or t.get("image_urls"))[:3],
        "video_urls": _parse_urls(t.get("resultJson") or t.get("result_urls"))[:3],
    }


def submit_task(
    gen_type: str,
    prompt: str,
    segments: Optional[str] = None,
    video_model: Optional[str] = None,
    style_prompt: Optional[str] = None,
    negative_prompt: Optional[str] = None,
    image_count: Optional[int] = None,
    direct_image: Optional[bool] = None,
    source: Optional[str] = None,
    slideshow_images: Optional[str] = None,
    slide_seconds: Optional[float] = None,
) -> dict:
    """提交一个生成任务到 Java 侧（Java 转发给 agent-service 执行）。

    gen_type: text_video(一句话生成视频) / image_video(图生视频) / text_image(文生图)
    segments: 画布模式片段数组 JSON 字符串 [{image_url, prompt, seconds, aspect_ratio}]
    slideshow_images: 图片合成视频的图片 URL 数组 JSON 字符串（纯本地 ffmpeg，不消耗额度）
    direct_image/image_count: 文生图直出（跳过 LLM 拆镜），一次出 image_count 张候选
    source: 画布素材传 "canvas_asset"（画廊不展示这些中间素材任务）
    """
    payload: dict[str, Any] = {
        "genType": gen_type,
        "prompt": prompt,
    }
    optional = {
        "segments": segments,
        "videoModel": video_model,
        "stylePrompt": style_prompt,
        "negativePrompt": negative_prompt,
        "imageCount": image_count,
        "directImage": direct_image,
        "source": source,
        "slideshowImages": slideshow_images,
        "slideSeconds": slide_seconds,
    }
    payload.update({k: v for k, v in optional.items() if v is not None})
    data = _post("/api/tasks/video", payload)
    data = data if isinstance(data, dict) else {}
    return {
        "task_id": data.get("id"),
        "status": data.get("status"),
        "gen_type": gen_type,
        "message": (
            f"任务已提交（id={data.get('id')}，类型 {gen_type}）。"
            "生成是异步的：用 get_task(task_id) 查进度，不要重复提交同一个提示词。"
        ),
    }


def _collect_finished(canvas_id: int, results: dict[int, dict]) -> dict:
    """把已完成任务的图写回对应节点（按 prompt 精确/归一化匹配），一次保存。

    只在节点还没有图时才填 —— 用户可能自己上传/挑过了，覆盖等于毁掉他的选择。
    """
    by_prompt: dict[str, list[str]] = {}
    loose: dict[str, list[str]] = {}
    for tid, r in results.items():
        urls = r.get("image_urls") or []
        if r.get("status") != "completed" or not urls:
            continue
        by_prompt[str(r.get("prompt") or "").strip()] = urls
        nk = _normalize_prompt(str(r.get("prompt") or ""))
        if nk:
            loose.setdefault(nk, urls)
    if not by_prompt and not loose:
        return {"filled": [], "saved": False}

    snap = _get_canvas(canvas_id)
    filled: list[str] = []
    for n in snap["nodes"]:
        if n.get("type") != "imageNode":
            continue
        d = n.get("data") or {}
        if str(d.get("imageUrl") or "").strip():
            continue
        key = str(d.get("prompt") or "").strip()
        urls = by_prompt.get(key) or loose.get(_normalize_prompt(key))
        if not urls:
            continue
        d["candidates"] = urls
        d["imageUrl"] = urls[0]
        filled.append(n.get("id"))
    if not filled:
        return {"filled": [], "saved": False}
    res = _save_canvas(canvas_id, snap["nodes"], snap["edges"], snap["wrapper"], snap["version"])
    if res.get("conflict"):
        out = _conflict(canvas_id, res, "回填生成的图片", snap["version"])
        out["filled_node_ids"] = filled
        out["hint"] = "图已经生成好了（任务上有 URL），只是没能写进画布；重读画布后再回填一次即可，不必重新生成。"
        return out
    canvas = res.get("canvas") or {}
    return {"filled": filled, "saved": True, "version": canvas.get("version")}


def generate_images(
    canvas_id: int,
    node_ids: Optional[list[str]] = None,
    count: int = 3,
    dry_run: bool = True,
    wait_seconds: int = 40,
) -> dict:
    """为画布上「有提示词但还没有图」的图片节点生成首帧（文生图，**按张计费**）。

    必须先 dry_run=True 报告计划并征得用户同意，再用 dry_run=False 执行。
    提交是并行的（一次把所有节点排进队列），随后在有界时间内收取已完成的结果。
    node_ids 省略 = 全部待生成节点。还在跑的任务会随 pending_task_ids 返回，
    用 collect_images(canvas_id, task_ids=[...]) 续收。
    """
    snap = _get_canvas(canvas_id)
    want = {str(i) for i in (node_ids or [])}
    targets: list[dict] = []
    for n in snap["nodes"]:
        if n.get("type") != "imageNode":
            continue
        d = n.get("data") or {}
        if str(d.get("imageUrl") or "").strip():
            continue
        prompt = str(d.get("prompt") or "").strip()
        if not prompt:
            continue
        if want and str(n.get("id")) not in want:
            continue
        targets.append({"node_id": n.get("id"), "prompt": prompt})
    if not targets:
        return {
            "canvas_id": canvas_id,
            "targets": [],
            "message": "没有待生成的图片节点（要么都已有图，要么提示词为空）。",
        }
    count = max(1, min(int(count or 1), 5))
    plan = {
        "canvas_id": canvas_id,
        "targets": [{**t, "prompt": t["prompt"][:120]} for t in targets],
        "count_per_node": count,
        "estimated_images": len(targets) * count,
    }
    # 成本闸门：超限直接拒绝——不依赖 LLM 自觉（它可能跳过 dry_run 直接执行）
    if not dry_run and len(targets) * count > MAX_IMAGES_PER_CALL:
        return {
            **plan,
            "dry_run": True,
            "refused": True,
            "message": (
                f"拒绝执行：{len(targets)} 个节点 × {count} 张 = {len(targets) * count} 张，"
                f"超过单次上限 {MAX_IMAGES_PER_CALL} 张。请缩小范围（传 node_ids 指定部分节点）"
                "或降低 count，再执行。"
            ),
        }

    if dry_run:
        return {
            **plan,
            "dry_run": True,
            "message": (
                f"计划：为 {len(targets)} 个节点各生成 {count} 张候选，合计 {len(targets) * count} 张（按张计费）。"
                "请先把这份清单和总张数告诉用户，得到同意后，再用 dry_run=False 执行。"
            ),
        }

    submitted: list[dict] = []
    task_to_target: dict[int, dict] = {}
    for t in targets:
        try:
            r = submit_task(
                gen_type="text_image",
                prompt=t["prompt"],
                direct_image=True,
                image_count=count,
                source="canvas_asset",
            )
            tid = r.get("task_id")
            submitted.append({"node_id": t["node_id"], "task_id": tid})
            if tid is not None:
                task_to_target[int(tid)] = t
        except Exception as exc:  # 单节点失败不影响整批（与前端批量流程一致）
            submitted.append({"node_id": t["node_id"], "error": str(exc)[:200]})

    results, pending = _poll_tasks(list(task_to_target.keys()),
                                   min(int(wait_seconds or 0), MAX_WAIT_SECONDS))
    for tid, r in results.items():
        r["prompt"] = task_to_target[tid]["prompt"]
    collected = _collect_finished(canvas_id, results)

    failed = [
        {"task_id": tid, "error": r.get("error") or r.get("status")}
        for tid, r in results.items()
        if r.get("status") != "completed"
    ]
    return {
        "submitted": submitted,
        "filled_node_ids": collected.get("filled", []),
        "saved": collected.get("saved", False),
        "conflict": collected.get("conflict", False),
        "pending_task_ids": pending,
        "failed": failed,
        "message": (
            f"已提交 {len(submitted)} 个生成任务；本次回填 {len(collected.get('filled', []))} 个节点，"
            f"仍在生成 {len(pending)} 个。"
            + ("仍在生成的任务可用 collect_images 续收。" if pending else "")
        ),
    }


def collect_images(canvas_id: int, task_ids: list[int], wait_seconds: int = 15) -> dict:
    """收取此前提交的文生图任务，把已完成的图回填进画布节点（按 prompt 匹配）。

    用于 generate_images 返回 pending_task_ids 之后续收，或用户隔一会儿说「图好了没」。
    """
    ids = [int(t) for t in (task_ids or []) if str(t).strip().lstrip("-").isdigit()]
    if not ids:
        return {"error": "task_ids 不能为空"}
    results, pending = _poll_tasks(ids, min(int(wait_seconds or 0), MAX_WAIT_SECONDS))
    # prompt 由 _poll_tasks 一并带回，不用再逐个 GET 一遍
    collected = _collect_finished(canvas_id, results)
    return {
        "filled_node_ids": collected.get("filled", []),
        "saved": collected.get("saved", False),
        "conflict": collected.get("conflict", False),
        "pending_task_ids": pending,
        "failed": [
            {"task_id": tid, "error": r.get("error") or r.get("status")}
            for tid, r in results.items()
            if r.get("status") != "completed"
        ],
        "message": (
            f"本次回填 {len(collected.get('filled', []))} 个节点；仍在生成 {len(pending)} 个。"
        ),
    }


def concat_task(task_id: int) -> dict:
    """把任务的分段视频拼成一条成片（纯本地 ffmpeg，不消耗生成额度，幂等）。

    仅当任务已完成、且分段 ≥ 2 段时才有意义；画布模式的任务在生成时已自动拼接。
    """
    t = _post(f"/api/tasks/{task_id}/concat")
    t = t if isinstance(t, dict) else {}
    return {
        "task_id": t.get("id", task_id),
        "status": t.get("status"),
        "urls": _parse_urls(t.get("resultJson"))[:3],
        "message": "已拼接成片（幂等：已有成片直接返回）",
    }


def _poll_tasks(task_ids: list[int], wait_seconds: int,
                interval: float = 4.0) -> tuple[dict[int, dict], list[int]]:
    """轮询到全部出结果或超时。返回 ({task_id: {status, image_urls, error}}, 未完成的 id)。"""
    results: dict[int, dict] = {}
    pending = [int(t) for t in task_ids]
    deadline = time.time() + max(0, int(wait_seconds))
    while True:
        for tid in list(pending):
            try:
                t = _get(f"/api/tasks/{tid}")
            except Exception as exc:
                results[tid] = {"status": "unknown", "error": str(exc)[:200]}
                pending.remove(tid)
                continue
            t = t if isinstance(t, dict) else {}
            status = t.get("status")
            if status == "completed":
                results[tid] = {
                    "status": status,
                    "prompt": t.get("prompt") or "",
                    "image_urls": _parse_urls(t.get("imageUrls") or t.get("image_urls")),
                }
                pending.remove(tid)
            elif status in ("failed", "expired"):
                results[tid] = {
                    "status": status,
                    "prompt": t.get("prompt") or "",
                    "error": t.get("errorMessage") or t.get("error_message") or status,
                }
                pending.remove(tid)
        if not pending or time.time() >= deadline:
            return results, pending
        time.sleep(interval)


# === 画布结构工具 ==========================================================
# 背景：让 LLM 回显整份 nodes 的 save_canvas 已移除 —— 画布 28 个节点 ≈ 12KB JSON，
# 回显必然丢节点（乐观锁只保证版本对，不保证内容对）。
# 结构改动因此改为**服务端定点操作**：LLM 只传少量参数，节点/连线的拼装在 Python 侧
# 完成，并复用同一套乐观锁（version 不符就不写、把服务端现状交回给 LLM）。


def _next_image_id(nodes: list) -> str:
    """挑一个没被占用的图片节点 id（沿用画布既有命名 img0 / img1 / …）。"""
    used = {str(n.get("id")) for n in nodes}
    for i in range(500):
        cand = f"img{i}"
        if cand not in used:
            return cand
    return f"img{len(nodes)}"


def _compose_id(nodes: list, edges: list) -> Optional[str]:
    """找「成片汇点」：约定是 id 为 compose 的节点；没有就取图片节点出边里出现最多的目标。"""
    ids = {str(n.get("id")) for n in nodes}
    if "compose" in ids:
        return "compose"
    img_ids = {str(n.get("id")) for n in nodes if n.get("type") == "imageNode"}
    counter: dict[str, int] = {}
    for e in edges:
        if str(e.get("source")) in img_ids:
            tgt = str(e.get("target"))
            if tgt in ids:
                counter[tgt] = counter.get(tgt, 0) + 1
    return max(counter, key=counter.get) if counter else None


def _pos(node: dict) -> tuple[float, float]:
    p = node.get("position") or {}
    return float(p.get("x") or 0), float(p.get("y") or 0)


def add_image_node(canvas_id: int, prompt: str, after_node_id: Optional[str] = None,
                   ratio: str = "16:9") -> dict:
    """在画布上新增一个图片节点（先把提示词填好），并自动连到成片节点。

    成片顺序 = 图片节点**从左到右的 x 坐标**，所以新节点默认排在最后；
    传 after_node_id 可插到某个节点之后（两者 x 相差 340）。
    """
    if not prompt or not prompt.strip():
        return {"error": "prompt 不能为空"}
    snap = _get_canvas(canvas_id)
    nodes, edges = snap["nodes"], snap["edges"]
    imgs = [n for n in nodes if n.get("type") == "imageNode"]
    xs = [_pos(n)[0] for n in imgs] or [60.0]
    ys = [_pos(n)[1] for n in imgs] or [60.0]

    if after_node_id:
        anchor = next((n for n in nodes if str(n.get("id")) == after_node_id), None)
        if anchor is None:
            return {"error": f"节点 {after_node_id} 不存在"}
        ax, ay = _pos(anchor)
        x, y = ax + 340, ay
    else:
        x, y = max(xs) + 340, min(ys)

    new_id = _next_image_id(nodes)
    nodes.append({
        "id": new_id, "type": "imageNode", "position": {"x": x, "y": y},
        "data": {"imageUrl": "", "prompt": prompt.strip(), "ratio": ratio or "16:9"},
    })
    sink = _compose_id(nodes, edges)
    if sink:
        edges.append({"id": f"{new_id}-ic", "source": new_id, "target": sink})

    res = _save_canvas(canvas_id, nodes, edges, snap["wrapper"], snap["version"])
    if res.get("conflict"):
        return _conflict(canvas_id, res, f"新增节点 {new_id}", snap["version"])
    canvas = res.get("canvas") or {}
    return {
        "saved": True,
        "node_id": new_id,
        "position": {"x": x, "y": y},
        "connected_to": sink,
        "version": canvas.get("version"),
        "message": (
            f"已新增图片节点 {new_id}"
            + (f"（插在 {after_node_id} 之后）" if after_node_id else "（排在最后）")
            + (f"，并连到成片节点 {sink}" if sink else "")
            + "。它现在是「待生成」，需要出图才有画面上成片。"
        ),
    }


def delete_node(canvas_id: int, node_id: str) -> dict:
    """删除画布上的一个节点，连同它的连线。

    **不允许删成片节点**（compose）：它是所有分段的汇点，删掉整条链就断了。
    """
    snap = _get_canvas(canvas_id)
    nodes, edges = snap["nodes"], snap["edges"]
    if next((n for n in nodes if str(n.get("id")) == node_id), None) is None:
        return {"error": f"节点 {node_id} 不存在"}
    if node_id == _compose_id(nodes, edges):
        return {"error": "不能删除成片节点（它是所有分段的汇点，删掉整条链就断了）；"
                         "要减镜请删对应的图片节点"}

    kept_nodes = [n for n in nodes if str(n.get("id")) != node_id]
    kept_edges = [e for e in edges
                  if str(e.get("source")) != node_id and str(e.get("target")) != node_id]
    res = _save_canvas(canvas_id, kept_nodes, kept_edges, snap["wrapper"], snap["version"])
    if res.get("conflict"):
        return _conflict(canvas_id, res, f"删除节点 {node_id}", snap["version"])
    canvas = res.get("canvas") or {}
    return {
        "saved": True,
        "deleted": node_id,
        "removed_edges": len(edges) - len(kept_edges),
        "remaining_nodes": len(kept_nodes),
        "version": canvas.get("version"),
        "message": f"已删除节点 {node_id} 及它的连线（剩余 {len(kept_nodes)} 个节点）",
    }


def connect_nodes(canvas_id: int, source: str, target: str) -> dict:
    """在画布上连一条线 source → target（重复连线/自环会被拒绝）。"""
    if source == target:
        return {"error": "不能把节点连到自己"}
    snap = _get_canvas(canvas_id)
    nodes, edges = snap["nodes"], snap["edges"]
    ids = {str(n.get("id")) for n in nodes}
    for nid in (source, target):
        if nid not in ids:
            return {"error": f"节点 {nid} 不存在"}
    for e in edges:
        if str(e.get("source")) == source and str(e.get("target")) == target:
            return {"saved": False, "message": f"{source} → {target} 已经连过了（未改动）"}

    edges.append({"id": f"e-{source}-{target}", "source": source, "target": target})
    res = _save_canvas(canvas_id, nodes, edges, snap["wrapper"], snap["version"])
    if res.get("conflict"):
        return _conflict(canvas_id, res, f"连线 {source} → {target}", snap["version"])
    canvas = res.get("canvas") or {}
    return {
        "saved": True,
        "edge": f"{source} → {target}",
        "edge_count": len(edges),
        "version": canvas.get("version"),
        "message": f"已连线 {source} → {target}",
    }


def reorder_shots(canvas_id: int, node_ids: list[str]) -> dict:
    """按给定顺序重排分镜（**成片顺序 = 图片节点从左到右的 x 坐标**）。

    只动列出的节点：按 node_ids 的顺序给它们分配递增的 x，y 保持不变（避免上下重叠）。
    没列出的节点位置不动。
    """
    if not node_ids:
        return {"error": "node_ids 不能为空"}
    snap = _get_canvas(canvas_id)
    nodes, edges = snap["nodes"], snap["edges"]
    by_id = {str(n.get("id")): n for n in nodes}
    for nid in node_ids:
        n = by_id.get(str(nid))
        if n is None:
            return {"error": f"节点 {nid} 不存在"}
        if n.get("type") != "imageNode":
            return {"error": f"节点 {nid} 不是图片节点（只有分镜图片节点有先后顺序）"}

    ordered = [by_id[str(nid)] for nid in node_ids]
    base = min(_pos(n)[0] for n in ordered)
    for i, n in enumerate(ordered):
        x, y = _pos(n)
        n["position"] = {"x": base + i * 340, "y": y}

    res = _save_canvas(canvas_id, nodes, edges, snap["wrapper"], snap["version"])
    if res.get("conflict"):
        return _conflict(canvas_id, res, "重排分镜顺序", snap["version"])
    canvas = res.get("canvas") or {}
    return {
        "saved": True,
        "order": [str(n.get("id")) for n in ordered],
        "version": canvas.get("version"),
        "message": "已按给定顺序重排分镜（" + " → ".join(str(n.get("id")) for n in ordered) + "）",
    }


def list_tasks() -> dict:
    """列出最近的生成任务（含状态、错误消息、结果 URL）。

    Java 侧返回结构: {"data":{"list":[...]}}
    """
    data = _get("/api/tasks")
    # 兼容 {"list": [...]} 结构
    if isinstance(data, dict) and "list" in data:
        return data
    return {"list": data if isinstance(data, list) else []}
