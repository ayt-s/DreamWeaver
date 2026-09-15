"""会话持久化回归测试（Redis 快照 / 启动恢复 / 节点幂等守卫 / 提交即落 video_id / 心跳）。

覆盖任务要求的四类断言：
1. 快照读写往返（真实 Redis db1，测试自清理）
2. **不连续索引**的恢复合并（0 和 2 有、1 没有 → 绝不能错位成前缀）
3. 节点守卫幂等（第二次进入不重复调 LLM / 不重复生图，用 monkeypatch 计数）
4. video_id 提交即落（提交成功那一刻就写盘）

不触真实 Agnes API：LLM/gateway/poller 全部 mock。
"""
import asyncio
import copy
import uuid

import pytest

from app.state import CreativeSessionState, TaskStatus


# ==========================================================================
# 1. session_store 本身
# ==========================================================================

@pytest.mark.asyncio
async def test_state_and_progress_roundtrip_real_redis():
    """真实 Redis(db1) 读写往返：state / progress / active / TTL，结束后自清理。"""
    from app import session_store

    session_store.store.enabled = True  # conftest 默认关了，这里显式打开
    try:
        if not await session_store.ping():
            pytest.skip("本机 Redis 不可用，跳过集成测试")

        sid = f"test-persist-{uuid.uuid4().hex[:10]}"
        state = {
            "session_id": sid,
            "status": TaskStatus.VIDEO_GENERATING,  # str 枚举 → JSON 往返后退化成普通 str
            "video_model": "agnes-video-2.5-flash",
            "segments": [{"prompt": "a"}, {"prompt": "b"}],
            "storyboard": [{"shot_id": 0, "prompt_en": "p0"}],
        }
        try:
            await session_store.save_state(sid, state)
            await session_store.add_active(sid)
            await session_store.mark_submitted(sid, 0, "vid0")
            await session_store.mark_submitted(sid, 2, "vid2")
            await session_store.mark_done(sid, 2, "http://x/2.mp4", "vid2")

            # state 往返
            back = await session_store.load_state(sid)
            assert back is not None
            assert back["session_id"] == sid
            assert back["status"] == "video_generating"
            assert back["segments"][0]["prompt"] == "a"

            # progress 往返：done 的段会从 submitted 里摘掉
            prog = await session_store.load_progress(sid)
            assert prog["submitted"] == {"0": "vid0"}
            assert prog["done"] == {"2": {"url": "http://x/2.mp4", "id": "vid2"}}

            # active 索引
            assert sid in await session_store.list_active()

            # TTL 契约：约 24h（>0 且 <= 86400）
            ttl = await session_store.store._client.ttl(
                session_store.STATE_KEY.format(sid=sid))
            assert 0 < ttl <= 86400
        finally:
            # 必须清掉自己的 key（不能污染真实 Redis）
            await session_store.delete_session(sid)
            assert await session_store.load_state(sid) is None
            assert await session_store.load_progress(sid) == {"submitted": {}, "done": {}}
            assert sid not in await session_store.list_active()
    finally:
        session_store.store.enabled = False
        await session_store.close()


@pytest.mark.asyncio
async def test_store_degrades_silently_when_disabled():
    """Redis 不可用/关闭时：所有方法静默降级，绝不抛异常（最重要的一条）。"""
    from app import session_store

    session_store.store.enabled = False
    assert await session_store.ping() is False
    await session_store.save_state("sid-x", {"session_id": "sid-x"})  # 不应抛
    await session_store.add_active("sid-x")
    await session_store.mark_submitted("sid-x", 0, "vid")
    await session_store.mark_done("sid-x", 0, "http://u", "vid")
    await session_store.clear_submitted("sid-x", 0)
    await session_store.delete_session("sid-x")
    assert await session_store.load_state("sid-x") is None
    assert await session_store.load_progress("sid-x") == {"submitted": {}, "done": {}}
    assert await session_store.list_active() == []
    # 锁降级为「放行」（本进程内恢复仍可进行）
    assert await session_store.acquire_lock("sid-x") is True


@pytest.mark.asyncio
async def test_store_degrades_on_unreachable_redis(monkeypatch):
    """Redis 连不上（端口拒绝）也必须静默降级，不让主流程挂掉。"""
    from app import session_store
    from app.config import settings

    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/1", raising=False)
    store = session_store.SessionStore()
    assert await store.ping() is False
    await store.save_state("sid-y", {"session_id": "sid-y"})  # 不应抛
    assert await store.load_state("sid-y") is None
    assert await store.list_active() == []
    await store.close()


# ==========================================================================
# 2. 启动恢复的合并逻辑（不连续索引是重点）
# ==========================================================================

def _recoverable_state(n_shots: int = 4) -> CreativeSessionState:
    return {
        "session_id": "rec-test",
        "user_id": "tester",
        "raw_prompt": "断点恢复",
        "gen_type": "image_video",
        "status": TaskStatus.VIDEO_GENERATING,
        "segments": [{"prompt": f"seg{i}"} for i in range(n_shots)],
        "storyboard": [
            {"shot_id": i, "prompt_en": f"p{i}", "seconds": "5",
             "aspect_ratio": "16:9", "mode": "text", "reference_images": []}
            for i in range(n_shots)
        ],
        # 快照里可能残留的旧视频数组（恢复必须清掉，见下）
        "video_urls": ["stale-0"],
        "video_ids": ["stale-id-0"],
        "trace": [],
    }


def test_merge_done_respects_noncontiguous_indices():
    """索引 0 和 2 完成、1 未完成 → 只能写到 0 和 2，绝不能按前缀错位到 0/1。"""
    from app.recovery import merge_done_into_state

    state = _recoverable_state(4)
    progress = {
        "submitted": {"1": "vid1"},
        "done": {
            "0": {"url": "http://x/0.mp4", "id": "vid0"},
            "2": {"url": "http://x/2.mp4", "id": "vid2"},
        },
    }

    written = merge_done_into_state(state, progress)

    assert written == 2
    # 按索引写回（storyboard + segments 都写，画布模式由 segments 重建 storyboard）
    for key in ("storyboard", "segments"):
        assert state[key][0]["existing_video_url"] == "http://x/0.mp4"
        assert "existing_video_url" not in state[key][1]      # ← 错位就会在这里挂
        assert state[key][2]["existing_video_url"] == "http://x/2.mp4"
        assert "existing_video_url" not in state[key][3]
    # video_urls / video_ids 必须清空（否则 video.py 的 done=len(video_urls) 语义错）
    assert state["video_urls"] == []
    assert state["video_ids"] == []


def test_merge_done_tolerates_empty_and_garbage_progress():
    """空 progress / 脏 key / 空 url 都不能抛异常，只清空 video_urls。"""
    from app.recovery import merge_done_into_state

    for progress in ({}, {"submitted": {}, "done": {}},
                     {"done": {"x": {"url": "u"}, "9": {"url": "u"}, "1": {}}}):
        state = _recoverable_state(2)
        assert merge_done_into_state(state, progress) == 0
        assert state["video_urls"] == []
        assert state["video_ids"] == []


class _ResultFuturePoller:
    """poller 替身：future 直接完成，并记录 submit 调用。"""

    def __init__(self) -> None:
        self.submitted: list[tuple] = []

    async def submit(self, video_id, model_name, session_id, shot_index,
                     provider="intl"):
        self.submitted.append((video_id, model_name, session_id, shot_index, provider))
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({"video_url": f"http://new/{video_id}.mp4", "video_id": video_id})
        return fut

    def get_future(self, video_id):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({"video_url": f"http://new/{video_id}.mp4", "video_id": video_id})
        return fut


@pytest.mark.asyncio
async def test_video_node_reuses_merged_noncontiguous_segments(monkeypatch):
    """恢复合并后跑 video_generator：只补生成缺的那两段，已完成的段零成本复用。"""
    from app.nodes import video as video_mod
    from app.nodes.video import video_generator_node
    from app.recovery import merge_done_into_state

    state = _recoverable_state(4)
    merge_done_into_state(state, {
        "submitted": {"1": "vid1"},
        "done": {
            "0": {"url": "http://x/0.mp4", "id": "vid0"},
            "2": {"url": "http://x/2.mp4", "id": "vid2"},
        },
    })

    generated: list[int] = []

    async def _fake_tool(prompt, seconds, mode, aspect_ratio, reference_images,
                         session_id, shot_index, model=None) -> dict:
        generated.append(shot_index)
        return {"video_id": f"new{shot_index}", "status": "submitted"}

    monkeypatch.setattr(video_mod, "generate_video_tool", _fake_tool)
    monkeypatch.setattr(video_mod, "poller", _ResultFuturePoller())

    result = await video_generator_node(state)

    # 只为「没有已完成产物」的 1、3 段重新提交（0/2 复用，不烧额度）
    assert generated == [1, 3]
    assert result["video_urls"] == [
        "http://x/0.mp4", "http://new/new1.mp4", "http://x/2.mp4", "http://new/new3.mp4",
    ]
    assert result["video_ids"] == ["reused-0", "new1", "reused-2", "new3"]


@pytest.mark.asyncio
async def test_video_node_reuses_inflight_pending_video_id(monkeypatch):
    """submitted 但未 done 的段：复用原 video_id 继续等，绝不重新提交。"""
    from app.nodes import video as video_mod
    from app.nodes.video import video_generator_node

    state = _recoverable_state(2)
    state["video_urls"] = []
    state["video_ids"] = []
    state["storyboard"][1]["pending_video_id"] = "inflight-1"

    generated: list[int] = []

    async def _fake_tool(prompt, seconds, mode, aspect_ratio, reference_images,
                         session_id, shot_index, model=None) -> dict:
        generated.append(shot_index)
        return {"video_id": f"new{shot_index}", "status": "submitted"}

    monkeypatch.setattr(video_mod, "generate_video_tool", _fake_tool)
    monkeypatch.setattr(video_mod, "poller", _ResultFuturePoller())

    result = await video_generator_node(state)

    assert generated == [0]  # 只有第 0 段是新提交的
    assert result["video_urls"] == [
        "http://new/new0.mp4", "http://new/inflight-1.mp4",
    ]


# ==========================================================================
# 3. 恢复流程：提交分流 + 重新入队
# ==========================================================================

class _FakeGateway:
    """按 provider 返回预设的 query_video 结果。"""

    provider_names = ["intl"]

    def __init__(self, results: dict) -> None:
        self.results = results
        self.queried: list[str] = []

    async def query_video(self, video_id, model_name, mode="text", provider_name=None):
        self.queried.append(video_id)
        return self.results.get(video_id, {})


@pytest.mark.asyncio
async def test_resolve_submitted_splits_completed_inflight_failed(monkeypatch):
    """submitted 的三类分流：已完成落 done / 仍生成中转 pending / 失败清掉重生成。"""
    from app import recovery, session_store

    done_calls, cleared_calls = [], []

    async def _fake_mark_done(sid, idx, url, vid=""):
        done_calls.append((idx, url, vid))

    async def _fake_clear_submitted(sid, idx):
        cleared_calls.append(idx)

    monkeypatch.setattr(session_store, "mark_done", _fake_mark_done)
    monkeypatch.setattr(session_store, "clear_submitted", _fake_clear_submitted)

    fake_poller = _ResultFuturePoller()
    monkeypatch.setattr(recovery, "poller", fake_poller)
    monkeypatch.setattr(recovery, "gateway", _FakeGateway({
        "vid-done": {"status": "completed", "url": "http://x/done.mp4"},
        "vid-run": {"status": "processing", "progress": 40},
        "vid-fail": {"status": "failed", "error": "boom"},
    }))

    state = _recoverable_state(3)
    progress = {"submitted": {"0": "vid-done", "1": "vid-run", "2": "vid-fail"},
                "done": {}}

    resolved = await recovery.resolve_submitted("rec-test", state, progress)

    assert resolved == 1
    # 已完成 → done（零成本）
    assert progress["done"]["0"] == {"url": "http://x/done.mp4", "id": "vid-done"}
    # 仍生成中 → 标 pending_video_id + 重新挂 poller，不重新提交
    assert state["storyboard"][1]["pending_video_id"] == "vid-run"
    assert fake_poller.submitted[0][0] == "vid-run"
    assert "1" in progress["submitted"]
    # 失败 → 从 submitted 摘掉，交给正常重生成
    assert cleared_calls == [2]
    assert "2" not in progress["submitted"]
    assert done_calls == [(0, "http://x/done.mp4", "vid-done")]


@pytest.mark.asyncio
async def test_recover_session_requeues_with_merged_state(monkeypatch):
    """恢复单会话：先回填 _sessions 再 scheduler.submit，并清掉视频数组。"""
    import app.main as main_mod
    from app import recovery, session_store

    sid = f"rec-{uuid.uuid4().hex[:8]}"
    snapshot = copy.deepcopy(_recoverable_state(3))
    snapshot["session_id"] = sid
    progress = {"submitted": {}, "done": {"2": {"url": "http://x/2.mp4", "id": "v2"}}}

    async def _load_state(_sid):
        return copy.deepcopy(snapshot)

    async def _load_progress(_sid):
        return copy.deepcopy(progress)

    async def _acquire_lock(_sid):
        return True

    async def _mark_done(*a, **kw):
        return None

    monkeypatch.setattr(session_store, "load_state", _load_state)
    monkeypatch.setattr(session_store, "load_progress", _load_progress)
    monkeypatch.setattr(session_store, "acquire_lock", _acquire_lock)
    monkeypatch.setattr(session_store, "mark_done", _mark_done)

    submitted: list[str] = []
    monkeypatch.setattr(recovery.scheduler, "submit", lambda s: submitted.append(s))
    monkeypatch.setattr(recovery, "gateway", _FakeGateway({}))
    monkeypatch.setattr(recovery, "poller", _ResultFuturePoller())

    saved = dict(main_mod._sessions)
    try:
        ok = await recovery.recover_session(sid)
        assert ok is True
        assert submitted == [sid]
        # 复用字段已写回，video_urls 已清空
        assert main_mod._sessions[sid]["storyboard"][2]["existing_video_url"] == "http://x/2.mp4"
        assert main_mod._sessions[sid]["video_urls"] == []
        # 幂等：同一 sid 不会被本进程恢复两次
        assert await recovery.recover_session(sid) is False
    finally:
        main_mod._sessions.clear()
        main_mod._sessions.update(saved)
        recovery._recovered.discard(sid)


@pytest.mark.asyncio
async def test_recover_session_skips_terminal_state(monkeypatch):
    """快照已是终态 → 不做恢复，只清理索引。"""
    from app import recovery, session_store

    deleted: list[str] = []
    submitted: list[str] = []

    async def _load_state(_sid):
        return {"session_id": "term-1", "status": "completed"}

    async def _load_progress(_sid):
        return {"submitted": {}, "done": {}}

    async def _acquire_lock(_sid):
        return True

    async def _delete_session(sid):
        deleted.append(sid)

    monkeypatch.setattr(session_store, "load_state", _load_state)
    monkeypatch.setattr(session_store, "load_progress", _load_progress)
    monkeypatch.setattr(session_store, "acquire_lock", _acquire_lock)
    monkeypatch.setattr(session_store, "delete_session", _delete_session)
    monkeypatch.setattr(recovery.scheduler, "submit", lambda s: submitted.append(s))

    try:
        assert await recovery.recover_session("term-1") is False
        assert deleted == ["term-1"]
        assert submitted == []
    finally:
        recovery._recovered.discard("term-1")


@pytest.mark.asyncio
async def test_recover_session_missing_snapshot_clears_active(monkeypatch):
    """快照丢了（过期/被清）→ 摘掉活跃索引，交给 Java 看门狗兜底。"""
    from app import recovery, session_store

    removed, submitted = [], []

    async def _load_state(_sid):
        return None

    async def _acquire_lock(_sid):
        return True

    async def _remove_active(sid):
        removed.append(sid)

    monkeypatch.setattr(session_store, "load_state", _load_state)
    monkeypatch.setattr(session_store, "acquire_lock", _acquire_lock)
    monkeypatch.setattr(session_store, "remove_active", _remove_active)
    monkeypatch.setattr(recovery.scheduler, "submit", lambda s: submitted.append(s))

    try:
        assert await recovery.recover_session("gone-1") is False
        assert removed == ["gone-1"]
        assert submitted == []
    finally:
        recovery._recovered.discard("gone-1")


@pytest.mark.asyncio
async def test_recover_active_sessions_never_raises(monkeypatch):
    """启动恢复整体兜底：内部异常绝不让服务启动流程挂掉。"""
    from app import recovery, session_store

    async def _boom():
        raise RuntimeError("redis 爆炸")

    monkeypatch.setattr(session_store, "list_active", _boom)
    await recovery.recover_active_sessions()  # 不应抛


@pytest.mark.asyncio
async def test_recover_active_sessions_iterates_active(monkeypatch):
    """遍历 active 集合，逐个恢复；单个失败不影响其它。"""
    from app import recovery, session_store

    seen: list[str] = []

    async def _list_active():
        return ["a-1", "b-2"]

    async def _recover_one(sid):
        seen.append(sid)
        if sid == "a-1":
            raise RuntimeError("坏快照")

    monkeypatch.setattr(session_store, "list_active", _list_active)
    monkeypatch.setattr(recovery, "recover_session", _recover_one)

    await recovery.recover_active_sessions()
    assert seen == ["a-1", "b-2"]


# ==========================================================================
# 4. 节点幂等守卫（第二次进入不重复花钱）
# ==========================================================================

class _CountingGateway:
    """记录调用次数的假网关。"""

    def __init__(self, script_json: str = "[]") -> None:
        self.calls: list[str] = []
        self.script_json = script_json

    async def chat(self, prompt, model=None, temperature=None, max_tokens=None,
                   session_id=None) -> str:
        self.calls.append(prompt)
        if "解析为结构化 Brief" in prompt:
            return ('{"theme":"主题","style":"风格","duration_seconds":"5",'
                    '"audience":"受众","mood":"情绪"}')
        if "Translate the following" in prompt:
            return "Translated English prompt."
        return self.script_json


@pytest.mark.asyncio
async def test_parser_guard_skips_llm_on_reentry(monkeypatch):
    from app.nodes import parser as parser_mod

    fake = _CountingGateway()
    monkeypatch.setattr(parser_mod, "gateway", fake)
    state: CreativeSessionState = {
        "session_id": "g-1", "user_id": "u", "raw_prompt": "x",
        "status": TaskStatus.PENDING, "trace": [],
    }

    first = await parser_mod.requirement_parser_node(state)
    assert len(fake.calls) == 1
    assert first["brief"]["theme"] == "主题"

    # 第二次进入（断点恢复场景）：已有 brief → 不应再调 LLM
    state2 = {**state, **first}
    second = await parser_mod.requirement_parser_node(state2)
    assert second == {}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_script_guard_skips_llm_on_reentry(monkeypatch):
    from app.nodes import script as script_mod

    fake = _CountingGateway('[{"shot_id":1,"visual":"v","camera":"c","duration":5,'
                            '"style_note":"s"}]')
    monkeypatch.setattr(script_mod, "gateway", fake)
    state: CreativeSessionState = {
        "session_id": "g-2", "user_id": "u", "raw_prompt": "x",
        "status": TaskStatus.QUEUED, "trace": [],
        "brief": {"theme": "t", "style": "s", "duration_seconds": "5",
                  "audience": "a", "mood": "m"},
    }

    first = await script_mod.script_writer_node(state)
    assert len(fake.calls) == 1
    assert first["script"]

    state2 = {**state, **first}
    second = await script_mod.script_writer_node(state2)
    assert second == {}
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_storyboard_guard_skips_translation_on_reentry(monkeypatch):
    from app.nodes import storyboard as sb_mod

    fake = _CountingGateway()
    monkeypatch.setattr(sb_mod, "gateway", fake)
    state: CreativeSessionState = {
        "session_id": "g-3", "user_id": "u", "raw_prompt": "x",
        "status": TaskStatus.SCRIPT_WRITING, "trace": [],
        "script": [
            {"shot_id": 1, "visual": "v1", "camera": "c1", "duration": 5, "style_note": "s"},
            {"shot_id": 2, "visual": "v2", "camera": "c2", "duration": 5, "style_note": "s"},
        ],
    }

    first = await sb_mod.storyboarder_node(state)
    assert len(fake.calls) == 2  # 每镜一次翻译
    assert all(s["prompt_en"] for s in first["storyboard"])

    state2 = {**state, **first}
    second = await sb_mod.storyboarder_node(state2)
    assert second == {}
    assert len(fake.calls) == 2  # 没有新增翻译调用


@pytest.mark.asyncio
async def test_image_guard_skips_regeneration_on_reentry(monkeypatch):
    """已有完整 image_urls → 守卫按索引回填 existing_image_url 后直接返回，不再生图。"""
    from app.nodes import image as image_mod
    from app.nodes.image import image_generator_node

    calls: list[str] = []

    class _GW:
        async def generate_image(self, prompt, model=None, session_id=None):
            calls.append(prompt)
            return [f"http://mock/{prompt}.png"]

    monkeypatch.setattr(image_mod, "gateway", _GW())

    state: CreativeSessionState = {
        "session_id": "g-4",
        "user_id": "u",
        "raw_prompt": "x",
        "gen_type": "text_image",
        "status": TaskStatus.ASSET_GENERATING,
        "segments": [
            {"prompt": "seg0", "existing_image_url": ""},
            {"prompt": "seg1", "existing_image_url": ""},
            {"prompt": "seg2", "existing_image_url": ""},
        ],
        "storyboard": [{"id": i, "prompt": f"seg{i}"} for i in range(3)],
        "image_urls": ["http://old/0.png", "http://old/1.png", "http://old/2.png"],
        "trace": [],
    }

    result = await image_generator_node(state)

    assert calls == []  # 一张都不重新生成
    assert result["image_urls"] == ["http://old/0.png", "http://old/1.png", "http://old/2.png"]
    # 按索引回填成既有复用分支读的字段名
    assert [s["existing_image_url"] for s in state["segments"]] == result["image_urls"]
    assert result["status"] == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_image_guard_regenerates_only_missing(monkeypatch):
    """既有图不齐：已有的复用、缺的补生成（不重复烧已完成的那些）。"""
    from app.nodes import image as image_mod
    from app.nodes.image import image_generator_node

    calls: list[str] = []

    class _GW:
        async def generate_image(self, prompt, model=None, session_id=None):
            calls.append(prompt)
            return [f"http://mock/{prompt}.png"]

    monkeypatch.setattr(image_mod, "gateway", _GW())

    state: CreativeSessionState = {
        "session_id": "g-5",
        "user_id": "u",
        "raw_prompt": "x",
        "gen_type": "text_image",
        "status": TaskStatus.ASSET_GENERATING,
        "segments": [{"prompt": f"seg{i}", "existing_image_url": ""} for i in range(3)],
        "image_urls": ["http://old/0.png", "", ""],  # 只有第 0 张有
        "trace": [],
    }

    result = await image_generator_node(state)

    assert calls == ["seg1", "seg2"]  # 0 复用，1/2 才调用
    assert result["image_urls"] == [
        "http://old/0.png", "http://mock/seg1.png", "http://mock/seg2.png",
    ]


# ==========================================================================
# 5. video_id 提交即落（全方案最关键的一行）
# ==========================================================================

@pytest.mark.asyncio
async def test_video_id_persisted_at_submit_time(monkeypatch):
    """提交成功那一刻就落盘 video_id，且早于等待 future（进程随后被杀也能查回）。"""
    from app import session_store
    from app.nodes import video as video_mod
    from app.nodes.video import video_generator_node

    order: list[tuple] = []

    async def _fake_mark_submitted(sid, idx, vid):
        order.append(("mark_submitted", idx, vid))

    async def _fake_mark_done(sid, idx, url, vid=""):
        order.append(("mark_done", idx, url, vid))

    class _Poller:
        def get_future(self, video_id):
            order.append(("get_future", video_id))
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            fut.set_result({"video_url": f"http://new/{video_id}.mp4", "video_id": video_id})
            return fut

    async def _fake_tool(prompt, seconds, mode, aspect_ratio, reference_images,
                         session_id, shot_index, model=None) -> dict:
        return {"video_id": f"vid{shot_index}", "status": "submitted"}

    monkeypatch.setattr(session_store, "mark_submitted", _fake_mark_submitted)
    monkeypatch.setattr(session_store, "mark_done", _fake_mark_done)
    monkeypatch.setattr(video_mod, "generate_video_tool", _fake_tool)
    monkeypatch.setattr(video_mod, "poller", _Poller())

    state = _recoverable_state(2)
    state["video_urls"] = []
    state["video_ids"] = []

    await video_generator_node(state)

    assert ("mark_submitted", 0, "vid0") in order
    assert ("mark_submitted", 1, "vid1") in order
    # 提交即落必须早于该段的 future 等待
    assert order.index(("mark_submitted", 0, "vid0")) < order.index(("get_future", "vid0"))
    # 完成后再落 done
    assert ("mark_done", 0, "http://new/vid0.mp4", "vid0") in order


# ==========================================================================
# 7. 端到端：恢复后的画布状态跑真图（不烧额度）
# ==========================================================================

@pytest.mark.asyncio
async def test_canvas_storyboarder_keeps_recovered_reuse_fields():
    """画布模式入口会**从 segments 重建 storyboard** →
    恢复写进 seg 的 existing_video_url / pending_video_id 必须被透传下去，否则全白费。"""
    from app.nodes.storyboard import canvas_storyboarder_node

    state: CreativeSessionState = {
        "session_id": "sb-rec",
        "segments": [
            {"image_url": "http://m/a.png", "prompt": "a", "prompt_en": "A", "seconds": 5,
             "existing_video_url": "http://x/0.mp4"},
            {"image_url": "http://m/b.png", "prompt": "b", "prompt_en": "B", "seconds": 5,
             "pending_video_id": "vid-inflight"},
        ],
    }

    out = await canvas_storyboarder_node(state)
    shots = out["storyboard"]

    assert shots[0]["existing_video_url"] == "http://x/0.mp4"
    assert shots[1]["pending_video_id"] == "vid-inflight"
    assert shots[1]["existing_video_url"] == ""


@pytest.mark.asyncio
async def test_full_chain_recovery_zero_cost_resume(monkeypatch):
    """端到端（零 agnes 调用）：恢复后的画布 session 跑真 LangGraph →
    已完成的段被复用、在飞的段被接住，**一段都不重新提交**，最终仍拼出成片。"""
    from app import graph
    from app.nodes import synthesizer as syn_mod
    from app.nodes import video as video_mod

    state: CreativeSessionState = {
        "session_id": f"e2e-recovery-{uuid.uuid4().hex[:8]}",
        "user_id": "tester",
        "raw_prompt": "断点续跑画布任务",
        "gen_type": "image_video",
        "segments": [
            {"image_url": "http://m/a.png", "prompt": "a", "prompt_en": "A", "seconds": 5,
             "existing_video_url": "http://x/0.mp4"},
            # 进程被杀时该段已提交 agnes、仍在生成 → 恢复流程写了 pending_video_id
            {"image_url": "http://m/b.png", "prompt": "b", "prompt_en": "B", "seconds": 5,
             "pending_video_id": "vid-inflight"},
            {"image_url": "http://m/c.png", "prompt": "c", "prompt_en": "C", "seconds": 5,
             "existing_video_url": "http://x/2.mp4"},
        ],
        "video_urls": [],
        "video_ids": [],
        "status": TaskStatus.VIDEO_GENERATING,
        "trace": [],
    }

    submitted: list[int] = []

    async def _fake_tool(prompt, seconds, mode, aspect_ratio, reference_images,
                         session_id, shot_index, model=None) -> dict:
        submitted.append(shot_index)
        return {"video_id": f"new{shot_index}", "status": "submitted"}

    async def _fake_download(url: str, dest, timeout=300.0) -> None:
        with open(dest, "wb") as f:
            f.write(b"\x00" * 1024)

    async def _fake_concat(inputs, output) -> bool:
        with open(output, "wb") as f:
            f.write(b"FAKEMP4")
        return True

    monkeypatch.setattr(video_mod, "generate_video_tool", _fake_tool)
    monkeypatch.setattr(video_mod, "poller", _ResultFuturePoller())
    monkeypatch.setattr(syn_mod, "download", _fake_download)
    monkeypatch.setattr(syn_mod, "concat_videos", _fake_concat)

    result = await graph.compiled_graph.ainvoke(
        state, config={"configurable": {"thread_id": state["session_id"]}})

    # 关键断言：一段都没有重新提交给 agnes
    assert submitted == []
    assert result["video_urls"] == [
        "http://x/0.mp4", "http://new/vid-inflight.mp4", "http://x/2.mp4",
    ]
    assert result["status"] == TaskStatus.COMPLETED
    assert result.get("final_video_url", "").startswith("/v1/files/")


@pytest.mark.asyncio
async def test_canvas_recovery_from_real_redis_roundtrip(monkeypatch):
    """真实 Redis 快照 → recover_session → 状态可直接被 video 节点零成本复用。"""
    import app.main as main_mod
    from app import recovery, session_store

    session_store.store.enabled = True
    try:
        if not await session_store.ping():
            pytest.skip("本机 Redis 不可用，跳过集成测试")

        sid = f"test-rec-{uuid.uuid4().hex[:10]}"
        snapshot = copy.deepcopy(_recoverable_state(3))
        snapshot["session_id"] = sid
        # 已完成 0 和 2；1 已提交且仍在生成
        await session_store.save_state(sid, snapshot)
        await session_store.add_active(sid)
        await session_store.mark_done(sid, 0, "http://x/0.mp4", "vid0")
        await session_store.mark_submitted(sid, 1, "vid-inflight")
        await session_store.mark_done(sid, 2, "http://x/2.mp4", "vid2")

        fake_poller = _ResultFuturePoller()
        monkeypatch.setattr(recovery, "poller", fake_poller)
        monkeypatch.setattr(recovery, "gateway", _FakeGateway({
            "vid-inflight": {"status": "processing", "progress": 30},
        }))
        submitted: list[str] = []
        monkeypatch.setattr(recovery.scheduler, "submit", lambda s: submitted.append(s))

        saved = dict(main_mod._sessions)
        try:
            assert await recovery.recover_session(sid) is True
            recovered = main_mod._sessions[sid]
            assert submitted == [sid]
            assert recovered["video_urls"] == []
            assert recovered["storyboard"][0]["existing_video_url"] == "http://x/0.mp4"
            assert recovered["storyboard"][2]["existing_video_url"] == "http://x/2.mp4"
            assert recovered["storyboard"][1]["pending_video_id"] == "vid-inflight"
            assert "existing_video_url" not in recovered["storyboard"][1]
            # 在飞的段重新挂了 future（继续等，而不是重新提交）
            assert fake_poller.submitted[0][0] == "vid-inflight"
        finally:
            main_mod._sessions.clear()
            main_mod._sessions.update(saved)
            recovery._recovered.discard(sid)
            # 恢复锁是刻意「不释放」的（TTL 5min），测试里必须自己清掉
            await session_store.release_lock(sid)
            await session_store.delete_session(sid)
            assert await session_store.load_state(sid) is None
            assert sid not in await session_store.list_active()
    finally:
        session_store.store.enabled = False
        await session_store.close()


# ==========================================================================
# 6. 心跳续期
# ==========================================================================

@pytest.mark.asyncio
async def test_heartbeat_no_op_without_java_url(monkeypatch):
    """JAVA_NOTIFY_URL 未配置 → 心跳直接返回，不做任何事。"""
    import app.main as main_mod
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "", raising=False)
    await asyncio.wait_for(main_mod._heartbeat_loop("hb-1"), timeout=1.0)


@pytest.mark.asyncio
async def test_heartbeat_posts_and_swallows_failures(monkeypatch):
    """心跳按 {java_notify_url}/internal/heartbeat POST {"session_id": ...}；
    失败必须静默降级（该接口由并行的 Java 子代理实现，现在还不存在）。"""
    import app.main as main_mod
    from app.config import settings

    monkeypatch.setattr(settings, "java_notify_url", "http://127.0.0.1:1", raising=False)
    monkeypatch.setattr(settings, "heartbeat_interval_s", 0.02, raising=False)

    hits: list[tuple] = []

    class _FailingClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            hits.append((url, json))
            raise ConnectionError("connection refused（Java 心跳接口还不存在）")

    monkeypatch.setattr(main_mod.httpx, "AsyncClient", _FailingClient)

    task = asyncio.create_task(main_mod._heartbeat_loop("hb-2"))
    await asyncio.sleep(0.15)
    alive_despite_failures = not task.done()   # 失败没有把协程打死
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert hits, "至少应尝试过一次心跳"
    assert hits[0][0].endswith("/internal/heartbeat")
    assert hits[0][1] == {"session_id": "hb-2"}
    assert alive_despite_failures
