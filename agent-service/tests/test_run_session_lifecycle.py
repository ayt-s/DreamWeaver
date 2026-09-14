"""回归测试：会话生命周期收尾 + 幂等守卫的状态语义（2026-09-14 评审 P0）。

两个都让「断点恢复」在最需要它的场景静默失效，且都是日常路径：

P0-1  `_run_session` 的 `finally` 原先无条件清 Redis 快照。
      `CancelledError` 继承自 `BaseException`，不被 `except Exception` 捕获，
      所以 `scheduler.stop() → w.cancel()`（Ctrl+C 优雅停止）也会走到 finally，
      把快照删掉 → 下次启动无法恢复。而硬杀（finally 不执行）反而能保留。
      结论是「优雅停止丢会话、硬杀能恢复」，与设计意图正好相反。

P0-2  `image_generator_node` 的幂等守卫早退分支无条件写 `status=COMPLETED`。
      对标准视频模式（text_video/image_video），`video_generator` 还在后面，
      却先落了一个终态 → 快照出现「假终态」→ 进程死在视频生成期间时，
      `recovery.recover_session` 判定会话已结束而放弃恢复。
"""
import asyncio

import pytest

from app.state import TaskStatus


class _FakeGraph:
    """替掉 compiled_graph：astream 只按给定的帧序列 yield，不真跑 LangGraph。"""

    def __init__(self, gen_factory):
        self._gen_factory = gen_factory

    def astream(self, state, config=None, stream_mode=None):  # noqa: ARG002
        return self._gen_factory(state)


# --------------------------------------------------------------- P0-1 取消语义

async def test_cancel_keeps_snapshot(monkeypatch):
    """被取消（Ctrl+C → scheduler.stop → w.cancel）时必须保留快照，待下次启动恢复。"""
    import app.main as main_mod

    deleted: list[str] = []
    started = asyncio.Event()

    async def fake_delete(sid):
        deleted.append(sid)

    async def fake_save(sid, state):  # noqa: ARG001
        return None

    async def never_ending(state):  # noqa: ARG001
        started.set()
        await asyncio.sleep(3600)
        yield {}  # pragma: no cover - 永不到达

    monkeypatch.setattr(main_mod.session_store, "delete_session", fake_delete)
    monkeypatch.setattr(main_mod.session_store, "save_state", fake_save)
    monkeypatch.setattr(main_mod, "compiled_graph", _FakeGraph(never_ending))

    task = asyncio.create_task(main_mod._run_session({"session_id": "cancel-001"}))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert deleted == [], "取消路径不得清快照（否则优雅停止会丢会话）"


async def test_normal_completion_clears_snapshot(monkeypatch):
    """正常跑完 = 终态 → 清快照（无需恢复）。"""
    import app.main as main_mod

    deleted: list[str] = []

    async def fake_delete(sid):
        deleted.append(sid)

    async def fake_save(sid, state):  # noqa: ARG001
        return None

    async def one_frame(state):  # noqa: ARG001
        yield {"session_id": "done-001", "status": TaskStatus.COMPLETED}

    monkeypatch.setattr(main_mod.session_store, "delete_session", fake_delete)
    monkeypatch.setattr(main_mod.session_store, "save_state", fake_save)
    monkeypatch.setattr(main_mod, "compiled_graph", _FakeGraph(one_frame))

    await main_mod._run_session({"session_id": "done-001"})

    assert deleted == ["done-001"], "正常完成应清快照"


async def test_node_failure_clears_snapshot(monkeypatch):
    """节点异常 → 已按 failed 落定（终态）→ 同样清快照，不应被误当成可恢复。"""
    import app.main as main_mod

    deleted: list[str] = []

    async def fake_delete(sid):
        deleted.append(sid)

    async def fake_save(sid, state):  # noqa: ARG001
        return None

    async def boom(state):  # noqa: ARG001
        raise RuntimeError("模拟节点异常")
        yield {}  # pragma: no cover

    monkeypatch.setattr(main_mod.session_store, "delete_session", fake_delete)
    monkeypatch.setattr(main_mod.session_store, "save_state", fake_save)
    monkeypatch.setattr(main_mod, "compiled_graph", _FakeGraph(boom))

    await main_mod._run_session({"session_id": "fail-001"})

    assert deleted == ["fail-001"], "失败落定后应清快照"


# ------------------------------------------------- P0-2 幂等守卫的状态语义

def _video_state() -> dict:
    return {
        "session_id": "img-video-001",
        "gen_type": "text_video",
        "image_urls": ["u0", "u1"],
        "storyboard": [{"prompt_en": "a"}, {"prompt_en": "b"}],
        "segments": [],
        "trace": [],
        "status": TaskStatus.ASSET_GENERATING,
    }


def _text_image_state() -> dict:
    return {
        "session_id": "img-text-001",
        "gen_type": "text_image",
        "image_urls": ["u0"],
        "storyboard": [{"prompt_en": "a"}],
        "segments": [],
        "trace": [],
        "status": TaskStatus.ASSET_GENERATING,
    }


@pytest.fixture
def _no_side_effects(monkeypatch):
    """守卫分支会发 SSE 事件与 Java 回调，单测里全部替换成 no-op。"""
    async def fake_emit(*args, **kwargs):
        return None

    async def fake_notify(**kwargs):
        return None

    monkeypatch.setattr("app.events.emit", fake_emit)
    monkeypatch.setattr("app.callback.java_notify.notify_java_completion", fake_notify)


async def test_image_guard_keeps_intermediate_status_for_video(_no_side_effects):
    """标准视频模式后面还有 video_generator → 不能写 COMPLETED（否则快照假终态）。"""
    from app.nodes.image import image_generator_node

    out = await image_generator_node(_video_state())

    assert out["status"] != TaskStatus.COMPLETED, \
        "标准视频模式写 COMPLETED 会让快照出现假终态，第二次死亡无法恢复"
    assert out["image_urls"] == ["u0", "u1"], "复用结果本身仍要返回"


async def test_image_guard_reports_completed_for_text_image(_no_side_effects):
    """文生图/漫剧是会话终点 → 必须仍是 COMPLETED（不能被上面的修复改坏）。"""
    from app.nodes.image import image_generator_node

    out = await image_generator_node(_text_image_state())

    assert out["status"] == TaskStatus.COMPLETED
    assert out["image_urls"] == ["u0"]
