"""MCP 调用审计：每次 tools/call 落一行到 SQLite。

为什么用 SQLite 而不是 MySQL：
- agent-service 目前不连 MySQL（业务数据全走 Java API），为了审计再引一个连接池是反的；
- 审计是追加写、按时间查，SQLite 单文件足够，克隆仓库的人不需要额外建库就能跑起来；
- 想接 MySQL / ClickHouse 时，换掉 record() 的实现即可，调用点不用动。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = _SERVICE_ROOT / "data" / "mcp_audit.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mcp_audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    token_name  TEXT    NOT NULL,
    tool        TEXT    NOT NULL,
    decision    TEXT    NOT NULL,          -- allowed / denied
    ok          INTEGER NOT NULL,          -- 1 成功 / 0 失败
    duration_ms REAL    NOT NULL,
    args_json   TEXT    NOT NULL DEFAULT '{}',
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_audit_tool ON mcp_audit_log(tool);
"""

_ARGS_MAX = 500


def db_path() -> Path:
    return Path(os.environ.get("MCP_AUDIT_DB") or DEFAULT_DB)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.executescript(_SCHEMA)
    return conn


def record(
    *,
    token: str,
    tool: str,
    decision: str,
    ok: bool,
    duration_ms: float,
    args: dict | None = None,
    error: str | None = None,
) -> None:
    """写一条审计。审计失败绝不打断正常调用（宁丢日志不丢请求）。"""
    payload = json.dumps(args or {}, ensure_ascii=False, default=str)[:_ARGS_MAX]
    try:
        with _connect(db_path()) as conn:
            conn.execute(
                "INSERT INTO mcp_audit_log (ts, token_name, tool, decision, ok, duration_ms, args_json, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), token, tool, decision, 1 if ok else 0, round(duration_ms, 2), payload, error),
            )
    except Exception:  # noqa: BLE001 — 审计不可用不能影响工具调用
        pass


def recent(limit: int = 20) -> list[dict]:
    """最近若干条审计，供本地核对 / 面试演示。"""
    path = db_path()
    if not path.exists():
        return []
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT ts, token_name, tool, decision, ok, duration_ms, args_json, error"
            " FROM mcp_audit_log ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [
        {
            "ts": r[0], "token": r[1], "tool": r[2], "decision": r[3],
            "ok": bool(r[4]), "duration_ms": r[5], "args": r[6], "error": r[7],
        }
        for r in rows
    ]


def stats() -> dict:
    """按工具统计调用量、失败数与平均耗时（演示/排障用）。"""
    path = db_path()
    if not path.exists():
        return {"total": 0, "by_tool": []}
    with _connect(path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM mcp_audit_log").fetchone()[0]
        rows = conn.execute(
            "SELECT tool, COUNT(*), SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END), AVG(duration_ms)"
            " FROM mcp_audit_log GROUP BY tool ORDER BY COUNT(*) DESC"
        ).fetchall()
    return {
        "total": total,
        "by_tool": [
            {"tool": r[0], "calls": r[1], "failures": r[2] or 0, "avg_ms": round(r[3] or 0, 2)}
            for r in rows
        ],
    }
