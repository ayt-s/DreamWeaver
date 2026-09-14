"""回归测试 P1-3：恢复/运行中的旧会话被用户「全量重生」后白烧 agnes 额度。

场景链：
1. Agent 崩了 → Java 看门狗把任务标成 `interrupted`
2. 用户点「全量重生」→ Java 换了新 session_id（旧会话已无人认领）
3. Agent 重启并自动恢复那个旧会话 → 它继续提交 agnes（白花钱），
   跑完的回调还会因 session_id 不匹配被 Java 丢弃

三道防线（本文件覆盖后两道，第一道在 Java 侧）：
- Java `regenerateTask` 先调 Agent `/v1/tasks/{sid}/cancel`（只能取消排队中的）
- Agent 心跳拿到 `tracked=false` → 置中止位 → 各节点在提交点前停止花钱
- Agent 启动恢复前先探测，明确无人认领就跳过恢复
"""
import asyncio

import pytest

from app import abort


class _FakeResp:
    def __init__(self, status_code=200, body=None, bad_json=False):
        self.status_code = status_code
        self._body = body
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    def __init__(self, resp=None, exc=None, **_kw):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):  # noqa: ARG002
        if self._exc:
            raise self._exc
        return self._resp


@pytest.fixture(autouse=True)
def _clean_abort():
    yield
    for sid in ("hb-001", "hb-002", "probe-001"):
        abort.clear(sid)


# ------------------------------------------------------------ abort 旁路信号

def test_abort_flag_mark_clear():
    assert abort.is_aborted("hb-001") is False
    abort.mark("hb-001")
    assert abort.is_aborted("hb-001") is True
    abort.clear("hb-001")
    assert abort.is_aborted("hb-001") is False
    # 空值安全
    assert abort.is_aborted("") is False
    abort.mark("")
    assert abort.is_aborted("") is False


# ------------------------------------------------ probe_session_tracked 三态

async def test_probe_returns_false_when_untracked(monkeypatch):
    from app.callback import java_notify
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "http://java-fake")
    monkeypatch.setattr(java_notify.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(200, {"code": 0, "data": {"tracked": False}})))

    assert await java_notify.probe_session_tracked("probe-001") is False


async def test_probe_returns_true_when_tracked(monkeypatch):
    from app.callback import java_notify
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "http://java-fake")
    monkeypatch.setattr(java_notify.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(200, {"code": 0, "data": {"tracked": True}})))

    assert await java_notify.probe_session_tracked("probe-001") is True


async def test_probe_returns_none_on_unreachable_or_bad_body(monkeypatch):
    """无法判定必须回 None —— 调用方据此保守放行，不能误判成「无人认领」。"""
    from app.callback import java_notify
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "http://java-fake")

    # 网络异常
    monkeypatch.setattr(java_notify.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(exc=RuntimeError("boom")))
    assert await java_notify.probe_session_tracked("probe-001") is None

    # 老版本 Java：200 但空体（曾经 handleHeartbeat 返回 void）
    monkeypatch.setattr(java_notify.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(200, None, bad_json=True)))
    assert await java_notify.probe_session_tracked("probe-001") is None

    # 非 200
    monkeypatch.setattr(java_notify.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(500, {"code": 500})))
    assert await java_notify.probe_session_tracked("probe-001") is None

    # 未配置 JAVA_NOTIFY_URL
    monkeypatch.setattr(settings, "java_notify_url", "")
    assert await java_notify.probe_session_tracked("probe-001") is None


# --------------------------------------------------- 心跳置位中止（核心链路）

async def test_heartbeat_marks_abort_when_untracked(monkeypatch):
    """心跳收到 tracked=false → 置中止位，节点后续不再提交 agnes。"""
    import app.main as main_mod
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "http://java-fake")
    monkeypatch.setattr(settings, "heartbeat_interval_s", 0.01)
    monkeypatch.setattr(main_mod.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(200, {"code": 0, "data": {"tracked": False}})))

    abort.clear("hb-001")
    task = asyncio.create_task(main_mod._heartbeat_loop("hb-001"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert abort.is_aborted("hb-001") is True


async def test_heartbeat_does_not_mark_when_tracked(monkeypatch):
    """tracked=true（任务仍被认领）绝不能置位，否则会误中止正在跑的会话。"""
    import app.main as main_mod
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "http://java-fake")
    monkeypatch.setattr(settings, "heartbeat_interval_s", 0.01)
    monkeypatch.setattr(main_mod.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(_FakeResp(200, {"code": 0, "data": {"tracked": True}})))

    abort.clear("hb-002")
    task = asyncio.create_task(main_mod._heartbeat_loop("hb-002"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert abort.is_aborted("hb-002") is False


# ------------------------------------------------- 启动恢复前的认领探测

async def test_recover_skips_when_untracked(monkeypatch):
    """明确无人认领 → 跳过恢复并清快照，不读 state、不入队。"""
    import app.recovery as rec
    from app import session_store

    async def fake_probe(sid):
        return False

    monkeypatch.setattr("app.callback.java_notify.probe_session_tracked", fake_probe)

    called: dict = {}

    async def fake_load(sid):
        called["load"] = sid
        return {"session_id": sid}

    async def fake_delete(sid):
        called["delete"] = sid

    async def fake_release(sid):
        called["release"] = sid

    monkeypatch.setattr(session_store, "load_state", fake_load)
    monkeypatch.setattr(session_store, "delete_session", fake_delete)
    monkeypatch.setattr(session_store, "release_lock", fake_release)

    ok = await rec.recover_session("untracked-001")

    assert ok is False
    assert "load" not in called, "无人认领时不应继续读快照/恢复"
    assert called.get("delete") == "untracked-001", "应清掉无人认领的残留快照"


async def test_recover_proceeds_when_probe_unknown(monkeypatch):
    """探测无法判定（Java 不可达）→ 保守照常恢复，不能因探测失败丢会话。"""
    import app.recovery as rec
    from app import session_store

    async def fake_probe(sid):
        return None

    monkeypatch.setattr("app.callback.java_notify.probe_session_tracked", fake_probe)

    called: dict = {}

    async def fake_load(sid):
        called["load"] = sid
        return None  # 无快照 → 走「清除活跃索引」分支，不再往下走

    async def fake_remove_active(sid):
        called["remove_active"] = sid

    monkeypatch.setattr(session_store, "load_state", fake_load)
    monkeypatch.setattr(session_store, "remove_active", fake_remove_active)
    monkeypatch.setattr(session_store, "release_lock", lambda sid: asyncio.sleep(0))

    ok = await rec.recover_session("unknown-001")

    assert ok is False
    assert called.get("load") == "unknown-001", "探测无法判定时应照常读快照"
