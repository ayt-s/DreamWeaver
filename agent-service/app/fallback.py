"""Java 回调失败的本地兜底存储。

场景：agent 完成视频任务后回调 Java /internal/notify，如果 Java 挂了（502/503/504/连接异常），
数据就丢了——用户看不到 gallery 里的作品。本模块把失败的回调写入本地 JSONL 文件，
等 Java 起来后调 /v1/internal/sync-fallback 端点主动拉走并落库，落库成功后删除本地记录。

存储格式（JSONL，一行一条回调 payload）：
{
  "id": "20260905_153045_abc123",
  "payload": { ...notify_java_completion 的完整 payload... },
  "attempts": 3,
  "last_error": "连接拒绝",
  "created_at": "2026-09-05T15:30:45+08:00"
}

设计取舍：
- 用 JSONL 而不是 SQLite：零依赖、崩溃不损坏、人工可读、Java 拉走就删
- 单文件追加：并发回调靠文件锁（fcntl/msvcrt）串行化，避免半行写入
- Java 拉走成功后删除文件条目，未成功的下次还能再拉

线程安全：asyncio 单事件循环内 append/drain 都是同步文件操作，用 asyncio.Lock 保护即可。
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 本地 fallback 存储文件（agent-service/data/fallback.jsonl）
FALLBACK_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "fallback.jsonl"

_lock = asyncio.Lock()


def _new_id(session_id: str) -> str:
    """生成幂等 id：时间戳 + session_id 前缀，避免撞 id。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    sid = (session_id or "nosession")[:12]
    return f"{ts}_{sid}"


async def append(payload: dict, error: str) -> str:
    """追加一条失败回调到本地 JSONL，返回记录 id。"""
    record = {
        "id": _new_id(payload.get("session_id", "")),
        "payload": payload,
        "attempts": int(payload.get("attempts", 0) or 0),
        "last_error": error[:500],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    line = __import__("json").dumps(record, ensure_ascii=False)
    async with _lock:
        FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(FALLBACK_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    logger.warning("回调失败已写入本地 fallback: id=%s session=%s err=%s",
                   record["id"], record["payload"].get("session_id", ""), error[:100])
    return record["id"]


async def list_pending() -> list[dict]:
    """读所有待同步记录（Java 拉走的）。"""
    if not FALLBACK_FILE.exists():
        return []
    import json as _json
    records: list[dict] = []
    async with _lock:
        with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(_json.loads(line))
                except _json.JSONDecodeError as e:
                    logger.error("fallback 文件第 %d 行损坏: %s", lineno, e)
    return records


async def mark_synced(ids: set[str]) -> int:
    """标记已同步的记录为已处理（从文件里删除）。返回删除条数。"""
    if not FALLBACK_FILE.exists() or not ids:
        return 0
    import json as _json
    removed = 0
    kept_lines: list[str] = []
    async with _lock:
        with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rec = _json.loads(stripped)
                if rec.get("id") in ids:
                    removed += 1
                    continue
            except _json.JSONDecodeError:
                pass  # 保留损坏行供排查
            kept_lines.append(line)
        with open(FALLBACK_FILE, "w", encoding="utf-8") as f:
            f.writelines(kept_lines)
    if removed:
        logger.info("fallback 已删除 %d 条已同步记录", removed)
    return removed


async def count() -> int:
    """当前 pending 记录数（用于健康检查/状态接口）。"""
    if not FALLBACK_FILE.exists():
        return 0
    async with _lock:
        with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
