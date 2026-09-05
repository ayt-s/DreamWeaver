"""内部同步端点：Java 启动/定时拉取 agent 本地 fallback 记录。

场景：
1. agent 回调 Java 失败（Java 挂了），数据写入 data/fallback.jsonl
2. Java 起来后调 GET /v1/internal/sync-fallback 拉取所有待同步记录
3. Java 逐条处理落库，然后调 POST /v1/internal/sync-fallback/ack 删除已处理记录
4. Java 侧启动时 @EventListener(ApplicationReadyEvent) 触发一次；每 5 分钟定时拉一次

端点：
- GET  /v1/internal/sync-fallback         列出所有待同步记录
- POST /v1/internal/sync-fallback/ack     标记已同步（body: {ids: [...]}}）
- GET  /v1/internal/sync-fallback/count   当前 pending 数量（健康检查用）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app import fallback

router = APIRouter(prefix="/v1/internal", tags=["internal"])


@router.get("/sync-fallback")
async def list_fallback() -> dict[str, Any]:
    """列出所有待同步的回调记录（Java 拉走逐条落库）。"""
    records = await fallback.list_pending()
    return {"code": 0, "message": "ok", "data": records}


@router.post("/sync-fallback/ack")
async def ack_fallback(body: dict) -> dict[str, Any]:
    """Java 落库成功后调用，把已处理的记录从本地文件删除。"""
    ids = set(body.get("ids") or [])
    removed = await fallback.mark_synced(ids)
    return {"code": 0, "message": "ok", "data": {"removed": removed}}


@router.get("/sync-fallback/count")
async def fallback_count() -> dict[str, Any]:
    """当前 pending 记录数（健康检查/监控用）。"""
    n = await fallback.count()
    return {"code": 0, "message": "ok", "data": {"pending": n}}
