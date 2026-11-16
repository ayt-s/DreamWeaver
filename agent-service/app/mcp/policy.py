"""MCP 访问策略：Token → 可见工具范围（scope）。

设计取舍：
- scope 只有 read / write 两级。工具级别细分（每个工具一个权限）在只有 6 个工具时是过度设计。
- write 天然包含 read（能改就能看的道理），避免配置时漏配导致 Agent 明明有写权限却调不了 list_tasks。
- stdio 模式（别人在自己机器上跑）默认只读：本地跑起来就能看任务、看画布，想生成图片必须显式开写权限，
  因为生成是**按张计费**的，默认放开等于给别人的钱包开口子。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

READ = "read"
WRITE = "write"

_SERVICE_ROOT = Path(__file__).resolve().parents[2]  # agent-service/
DEFAULT_TOKEN_FILE = _SERVICE_ROOT / ".mcp-tokens.json"


@dataclass(frozen=True)
class TokenInfo:
    name: str
    scopes: frozenset[str]

    def allows(self, scope: str) -> bool:
        if scope in self.scopes:
            return True
        # write ⇒ read
        return scope == READ and WRITE in self.scopes


def token_file() -> Path:
    return Path(os.environ.get("MCP_TOKEN_FILE") or DEFAULT_TOKEN_FILE)


def load_tokens(path: Path | None = None) -> dict[str, TokenInfo]:
    """读取 token 白名单。文件不存在时返回空字典（= HTTP 模式一律拒绝）。"""
    p = path or token_file()
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8") or "{}")
    out: dict[str, TokenInfo] = {}
    for token, meta in (raw.get("tokens") or {}).items():
        if not token or not isinstance(meta, dict):
            continue
        scopes = {s for s in (meta.get("scopes") or []) if s in (READ, WRITE)}
        if not scopes:
            continue
        out[token] = TokenInfo(name=str(meta.get("name") or token[:8]), scopes=frozenset(scopes))
    return out


def stdio_identity() -> TokenInfo:
    """stdio 模式的调用方身份。

    MCP_TOKEN 设置且能在白名单里找到时用白名单里的 scope；否则按本地开发默认：
    只读，除非 MCP_ALLOW_WRITE=1。
    """
    tokens = load_tokens()
    env_token = (os.environ.get("MCP_TOKEN") or "").strip()
    if env_token and env_token in tokens:
        return tokens[env_token]
    allow_write = (os.environ.get("MCP_ALLOW_WRITE") or "").strip() in {"1", "true", "yes"}
    scopes = {READ, WRITE} if allow_write else {READ}
    return TokenInfo(name="stdio-local", scopes=frozenset(scopes))
