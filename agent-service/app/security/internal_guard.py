"""内部接口的来源校验（agent 侧，与 Java 的 `InternalTokenFilter` 对称）。

为什么需要
----------
`/v1/internal/**` 在部署后会被 nginx 的 `/v1` 规则一并暴露到公网 ——
`GET /v1/internal/sync-fallback` 会把所有待同步记录（含提示词、产物 URL）明文吐出来。
阶段 1 之前靠"只在本机跑"糊过去，对外就是数据泄漏。

开关
----
`INTERNAL_TOKEN` 未配置时**放行**（本地开发不用改启动脚本），但打一次警告。
生产必须和 Java 侧配同一个值，且 Java 调用 agent 时要带同一个头
（见 `FallbackSyncScheduler`）。
"""
from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

HEADER = "X-Internal-Token"
_warned = False


async def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias=HEADER),
) -> None:
    """FastAPI 依赖：校验 `X-Internal-Token`。未配置时放行（仅开发）。"""
    global _warned
    expected = (settings.internal_token or "").strip()
    if not expected:
        if not _warned:
            _warned = True
            logger.warning(
                "/v1/internal/** 未配置 INTERNAL_TOKEN —— 内部接口当前不校验来源，"
                "仅可用于本地开发；上线前必须设置（Java 侧同名）。")
        return
    given = (x_internal_token or "").strip()
    if not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="内部接口鉴权失败")
