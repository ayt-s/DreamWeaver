"""终态收尾语义：`settle_session` 保留快照、但必须退出活跃索引。

## 为什么要这组测试

2026-09-17 用真实任务（Java 52）实测发现：任务**一完成**，`GET /v1/tasks/{sid}`
立刻 404 —— 前端轨迹面板拿不到 `trace`、逐镜质检明细一并消失，
而「哪一镜为什么没通过」恰恰是完成态最需要看的信息。

改法是把终态收尾从 `delete_session`（连快照一起删）换成 `settle_session`
（只 SREM active + 删 progress，快照留到 TTL）。

**这对性质必须同时成立**，少一条都会引入更严重的问题：
1. 快照要留着（否则改动没意义）
2. 活跃索引要退出（否则**启动恢复会把已完成的任务重新跑一遍** —— 重复烧额度 + 重复回调）

第 2 条是这次改动唯一的真风险，所以单独用 `recover_active_sessions` 跑一遍来验。
"""

import pytest

from app import session_store
from app.session_store import SessionStore


class _FakeRedis:
    """只实现 session_store 用到的那几个命令（避免测试依赖真实 Redis）。"""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if key in self.kv:
                del self.kv[key]
                removed += 1
        return removed

    async def srem(self, key, *members):
        bucket = self.sets.setdefault(key, set())
        removed = 0
        for m in members:
            if m in bucket:
                bucket.discard(m)
                removed += 1
        return removed

    async def sadd(self, key, *members):
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def set(self, key, value, ex=None):
        self.kv[key] = value
        return True

    async def get(self, key):
        return self.kv.get(key)


@pytest.fixture
def fake_redis(monkeypatch):
    """把模块级 store 换成用假 Redis 的实例（`_get_client` 直接给假客户端）。"""
    store = SessionStore()
    fake = _FakeRedis()
    monkeypatch.setattr(store, "_get_client", lambda: fake)
    monkeypatch.setattr(session_store, "store", store)
    return fake


@pytest.mark.asyncio
async def test_settle_keeps_state_snapshot_but_leaves_active(fake_redis):
    """★ 快照留着（能查 trace/qc_report）、活跃索引退出、progress 清掉。"""
    sid = "s-settle"
    await session_store.save_state(sid, {"session_id": sid, "status": "completed"})
    await session_store.add_active(sid)
    await session_store.save_progress(sid, {"submitted": {"0": "v1"}, "done": {}})

    await session_store.settle_session(sid)

    snapshot = await session_store.load_state(sid)
    assert snapshot is not None, "终态后快照必须还在，否则完成的任务查不到 trace"
    assert snapshot["session_id"] == sid
    assert sid not in await session_store.list_active()
    assert await session_store.load_progress(sid) == {"submitted": {}, "done": {}}


@pytest.mark.asyncio
async def test_settled_session_is_not_recovered(fake_redis, monkeypatch):
    """★ 核心安全前提：保留快照**不会**让启动恢复把它当活跃会话重跑。

    这条要是挂了，后果比原缺陷严重得多：已完成的任务会在每次服务重启时
    被重新生成一遍（重复烧额度 + 重复回调）。
    """
    sid = "s-done"
    await session_store.save_state(sid, {"session_id": sid, "status": "completed"})
    await session_store.add_active(sid)
    await session_store.settle_session(sid)

    from app import recovery

    recovered: list[str] = []

    async def _record(session_id):
        recovered.append(session_id)
        return True

    monkeypatch.setattr(recovery, "recover_session", _record)
    await recovery.recover_active_sessions()

    assert recovered == [], f"已终态会话被启动恢复捡起来重跑了: {recovered}"


@pytest.mark.asyncio
async def test_still_active_session_is_recovered(fake_redis, monkeypatch):
    """对照组：真正还在跑的会话必须被恢复（别把恢复能力一起改没了）。"""
    sid = "s-running"
    await session_store.save_state(sid, {"session_id": sid, "status": "video_generating"})
    await session_store.add_active(sid)

    from app import recovery

    recovered: list[str] = []

    async def _record(session_id):
        recovered.append(session_id)
        return True

    monkeypatch.setattr(recovery, "recover_session", _record)
    await recovery.recover_active_sessions()

    assert recovered == [sid]


@pytest.mark.asyncio
async def test_cancel_path_still_deletes_everything(fake_redis):
    """★ 取消排队那条路径必须**连快照一起清**。

    否则重启会把用户已经取消的任务捡回来跑 —— 所以 `delete_session` 保留原语义，
    不要顺手也换成 `settle_session`。
    """
    sid = "s-cancel"
    await session_store.save_state(sid, {"session_id": sid})
    await session_store.add_active(sid)

    await session_store.delete_session(sid)

    assert await session_store.load_state(sid) is None
    assert sid not in await session_store.list_active()


@pytest.mark.asyncio
async def test_settle_is_silent_when_redis_unavailable(monkeypatch):
    """Redis 不可用时静默降级（收尾路径绝不能把会话结果搞挂）。"""
    store = SessionStore()
    monkeypatch.setattr(store, "_get_client", lambda: None)
    monkeypatch.setattr(session_store, "store", store)

    await session_store.settle_session("s-no-redis")  # 不抛即通过
