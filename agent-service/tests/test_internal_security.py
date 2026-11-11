"""内部接口来源校验 + /health 探针（阶段 2）。

为什么写这两组：
- `/v1/internal/**` 在部署后会被 nginx 的 `/v1` 规则一并暴露，不做来源校验就是
  明文吐待同步记录（提示词、产物 URL）
- `/health` 此前**根本不存在**，进程被 SIGTERM 后只能靠"任务不动了"才发现
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import settings
from app.security.internal_guard import require_internal_token


@pytest.mark.asyncio
async def test_internal_guard_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_token", "s3cret")
    with pytest.raises(HTTPException) as ei:
        await require_internal_token(None)
    assert ei.value.status_code == 401

    with pytest.raises(HTTPException):
        await require_internal_token("wrong")

    # 正确令牌：不抛异常
    assert await require_internal_token("s3cret") is None


@pytest.mark.asyncio
async def test_internal_guard_allows_when_unconfigured(monkeypatch):
    """未配置时放行（本地开发不用改启动脚本），但生产必须配。"""
    monkeypatch.setattr(settings, "internal_token", "")
    assert await require_internal_token(None) is None


def test_health_route_registered():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/health" in paths


@pytest.mark.asyncio
async def test_health_reports_degraded_when_redis_down(monkeypatch):
    """Redis 不可用时：进程仍然活着（HTTP 200），但状态标 DEGRADED。"""
    from app import main as main_mod

    class _Boom:
        @staticmethod
        def from_url(*a, **kw):
            raise RuntimeError("redis down")

    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", _Boom.from_url)
    body = await main_mod.health()

    assert body["redis"] is False
    assert body["status"] == "DEGRADED"
    assert body["service"] == "agent-service"
