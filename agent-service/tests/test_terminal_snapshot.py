"""会话终态必须落到 Redis 快照（2026-09-19 修 #1·#9）。

## 缺陷
`_run_session` 的失败分支只写内存 `_sessions[sid]`，**不调 `save_state`**；
而 `_sessions` 1 小时后被 `_release_session` 释放 —— 之后 `GET /v1/tasks/{sid}` 只能读
Redis 快照，快照却停在最后一个 checkpoint 的状态。实测：DB 里 `failed` 的会话，
agent 侧一直报 `storyboard_writing`（任务 80）、DB 里 `completed` 的报 `asset_generating`（任务 79）。

危害：① 前端轨迹面板恰好在失败时看不到失败原因/逐镜明细；
② `recovery` 的终态守卫按快照 status 判断，拿不到终态就会**把已失败的会话从头重跑**（烧额度）。

同类：文生图/漫剧的图路由是 text_done → END，`image_generator_node` 是最后一步，
原来也返回 ASSET_GENERATING ⇒ 快照永远「资产生成中」，与已发出的 completed 回调矛盾。
"""
import asyncio

import pytest

from app.state import TaskStatus


class _RecordingStore:
    """记录 save_state 调用（真实 session_store 连 Redis，测试里不依赖）。"""

    def __init__(self):
        self.saved: list[tuple[str, dict]] = []

    async def save_state(self, sid, state):
        self.saved.append((sid, dict(state)))

    async def settle_session(self, sid):
        pass

    async def clear_submitted(self, sid, idx):
        pass

    async def mark_submitted(self, sid, idx, video_id):
        pass

    async def mark_done(self, sid, idx, url, video_id):
        pass


@pytest.mark.asyncio
async def test_failed_session_persists_terminal_snapshot(monkeypatch):
    """节点抛错 → 快照里必须是 FAILED + 错误文案（而不是停在最后一个 checkpoint）。"""
    from app import main as main_mod

    store = _RecordingStore()
    monkeypatch.setattr(main_mod, "session_store", store)

    class _BoomGraph:
        async def astream(self, state, config=None, stream_mode=None):
            yield {**state, "status": TaskStatus.STORYBOARD_WRITING}   # 最后一个 checkpoint
            raise RuntimeError("上游 503 video_queue_full")

    monkeypatch.setattr(main_mod, "compiled_graph", _BoomGraph())

    async def _noop_heartbeat(sid):
        return None

    monkeypatch.setattr(main_mod, "_heartbeat_loop", _noop_heartbeat)

    # 回调任务会被 create_task 发出（真实实现静默降级），这里同步掉避免噪声
    async def _noop_notify(**kwargs):
        return None

    import app.callback.java_notify as jn
    monkeypatch.setattr(jn, "notify_java_completion", _noop_notify)

    state = {
        "session_id": "term-snapshot-test",
        "user_id": "tester",
        "raw_prompt": "终态快照",
        "status": TaskStatus.QUEUED,
        "trace": [],
    }
    await asyncio.wait_for(main_mod._run_session(state), timeout=20)
    await asyncio.sleep(0)  # 让 create_task 的协程跑起来

    assert store.saved, "失败分支必须写快照（这正是 #1·#9 的修复点）"
    sid, snapshot = store.saved[-1]
    assert sid == "term-snapshot-test"
    assert snapshot["status"] == TaskStatus.FAILED
    assert snapshot.get("error_message")


@pytest.mark.asyncio
async def test_text_image_node_returns_terminal_status(monkeypatch):
    """直出图（text_image）：出图成功 → 节点必须返回 COMPLETED；全失败 → FAILED。"""
    from app.nodes import image as image_mod

    calls: dict = {}

    async def _fake_finish(session_id, storyboard, image_urls):
        calls["urls"] = list(image_urls)

    monkeypatch.setattr(image_mod, "_finish_text_image", _fake_finish)

    class _Gw:
        def __init__(self, urls):
            self._urls = urls

        async def generate_image(self, prompt, **kwargs):
            # 宽签名：直出图分支会额外传 ratio / reference_images 等
            return list(self._urls)

    from app.nodes.image import image_generator_node

    base = {
        "session_id": "ti-status-test",
        "user_id": "tester",
        "raw_prompt": "直出图",
        "gen_type": "text_image",
        "direct_image": True,   # 直出图分支的入口就是这个键（不是 gen_type）
        "status": TaskStatus.QUEUED,
        "trace": [],
    }

    monkeypatch.setattr(image_mod, "gateway", _Gw(["http://mock/a.png"]))
    ok = await image_generator_node({**base, "image_count": 1})
    assert ok["status"] == TaskStatus.COMPLETED, "出图成功却仍报生成中 → 快照永远停在中间态"
    assert calls["urls"] == ["http://mock/a.png"]

    monkeypatch.setattr(image_mod, "gateway", _Gw([]))
    bad = await image_generator_node({**base, "session_id": "ti-status-test-2", "image_count": 1})
    assert bad["status"] == TaskStatus.FAILED
