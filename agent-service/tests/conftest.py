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
def _quiet_side_effects(monkeypatch):
    """关掉对 Java 的副作用：不回调 8080、心跳不会真的发出去。

    单测不该依赖 Java 服务，也不该因为它在跑而对真实端口发请求。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "", raising=False)
    monkeypatch.setattr(settings, "heartbeat_interval_s", 3600, raising=False)


# === 测试隔离：产物目录 + asset_fetch 下载替身 ===
@pytest.fixture(autouse=True)
def _isolate_outputs_and_asset_fetch(monkeypatch, tmp_path):
    """把产物目录指到临时目录，并把 asset_fetch 的下载换成写本地占位文件。

    **为什么需要（A4 实测发现）**：`asset_fetch` 接入图之后，任何跑
    `compiled_graph` 的测试都会经过它。当时没有替身，实测 3 个端到端测试
    共发起 7 次真实网络请求去下载 `http://mock/...` —— 而且因为 asset_fetch
    **刻意吞掉单段异常并降级**（留空占位），**测试不会失败**，只是静默走了
    「全部下载失败」分支：既没覆盖到本地复用路径，又要等 4 次退避
    （5/15/45/90s，本机 DNS 快失败才侥幸没挂）。

    隔离产物目录则避免测试往仓库的 `agent-service/data/outputs/` 里写脏目录
    （此前已积累 test-001 / test-canvas / e2e-recovery-* 等）。

    需要自定义下载行为的测试（如 test_asset_fetch.py）在自己的用例里
    monkeypatch 覆盖即可 —— 用例内的 patch 晚于本夹具执行，优先级更高。
    """
    from pathlib import Path

    from app.nodes import asset_fetch as af

    async def _fake_download(url, dest, timeout=300.0):
        Path(dest).write_bytes(b"\x00" * 1024)

    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(af, "download", _fake_download)
