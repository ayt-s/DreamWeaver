"""测试全局夹具。

默认**关闭 Redis 会话持久化**（`app.session_store.store.enabled = False`）：
- 单测不应污染真实的 Redis db1（本机 6379 是活的，会真的写进去）
- 避免网络抖动 / Redis 不可用导致断言不稳定

需要真实 Redis 的集成测试在自己的用例里显式打开（见 test_session_persistence.py）。
"""
import pytest


@pytest.fixture(autouse=True)
def _disable_session_store():
    """所有测试默认让 session_store 静默降级为 no-op。"""
    from app import session_store

    prev = session_store.store.enabled
    session_store.store.enabled = False
    try:
        yield
    finally:
        session_store.store.enabled = prev


@pytest.fixture(autouse=True)
def _quiet_heartbeat(monkeypatch):
    """心跳间隔调到 1 小时，避免后台任务在测试里真的去 POST 8080。"""
    from app.config import settings

    monkeypatch.setattr(settings, "heartbeat_interval_s", 3600, raising=False)
