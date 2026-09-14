"""启动自动恢复：进程重启后把未完成的会话从 Redis 快照里捡回来，断点续跑。

调用点：`app/main.py` 的 `_startup()`（`scheduler.start()` 之后）
        → `await recover_active_sessions()`（实际以 create_task 异步执行，不阻塞启动）

核心机制（零成本续跑，不浪费已消耗的 agnes 额度）：

1. **state 快照回填 `_sessions`** —— `scheduler._run_one` 是从 `_sessions.get(sid)`
   取 state 的，所以必须**先回填再入队**。
2. **`progress.done` 的 url 按索引写回** storyboard / segments 各镜的 `existing_video_url`，
   并把 `state["video_urls"]`、`state["video_ids"]` 置空
   → `video.py` 的 `done = len(video_urls)` 得到 0，逐段循环自动命中**已存在且已验证**的
     `existing_video_url` 复用分支（零改动复用）。
   ⚠️ **必须按索引写回**，不能依赖 `enumerate` 的前缀假设——索引可能不连续
   （如 idx 0 和 2 已完成、1 还在飞）。
   ⚠️ 同时写进 `segments[idx]`，因为画布模式入口是 `canvas_storyboarder`，
   它会**从 segments 重建 storyboard**（只会保留 seg 里的 existing_video_url）。
3. **`progress.submitted` 但未 `done` 的 video_id：绝不重新提交**，改为查一次 agnes：
   - 已完成 → 落进 `done`（零成本）
   - 仍生成中 → 重新挂 `poller` future，并标记 `pending_video_id` 让节点复用它继续等
   - 失败/过期 → 从 submitted 移除，留空交给正常重生成
4. `_sessions[sid] = state` → `scheduler.submit(sid)` → POST `/internal/notify`
   `status=queued`（复用 app/callback/java_notify.py，让 Java 侧重新武装看门狗）。

整个恢复流程包在 try/except 里，**失败只 log，绝不阻塞服务启动**。
"""
import asyncio
import logging
from typing import Any

from app import session_store
from app.gateway.agnes import gateway
from app.poller import poller
from app.scheduler import scheduler

logger = logging.getLogger(__name__)

# 已被本进程恢复/接管过的会话（防止重复恢复）
_recovered: set[str] = set()

# progress.done 里 url 已就绪的判定 + 明确失败的状态白名单
_DONE_STATUS = ("completed", "done", "success", "succeeded")
_FAILED_STATUS = ("failed", "fail", "error", "expired", "canceled", "cancelled")


# --------------------------------------------------------------------- 工具函数

def _to_index(key: Any) -> int | None:
    """把 progress 的 key（JSON 往返后是字符串）转成段索引；非法返回 None。"""
    try:
        idx = int(str(key).strip())
    except (TypeError, ValueError):
        return None
    return idx if idx >= 0 else None


def _set_reuse_url(state: dict, idx: int, url: str) -> bool:
    """把已完成的视频 URL **按索引**写回各镜的 existing_video_url（storyboard + segments）。

    返回是否至少写入一处。
    """
    written = False
    storyboard = state.get("storyboard")
    if isinstance(storyboard, list) and 0 <= idx < len(storyboard):
        if isinstance(storyboard[idx], dict):
            storyboard[idx]["existing_video_url"] = url
            written = True
    segments = state.get("segments")
    if isinstance(segments, list) and 0 <= idx < len(segments):
        if isinstance(segments[idx], dict):
            segments[idx]["existing_video_url"] = url
            written = True
    return written


def _set_pending_video(state: dict, idx: int, video_id: str) -> None:
    """标记「该段已提交 agnes 且仍在生成」，让 video.py 复用原 video_id 继续等。"""
    for key in ("storyboard", "segments"):
        items = state.get(key)
        if isinstance(items, list) and 0 <= idx < len(items):
            if isinstance(items[idx], dict):
                items[idx]["pending_video_id"] = video_id


def merge_done_into_state(state: dict, progress: dict) -> int:
    """把 progress.done 按索引合并回 state，并把 video_urls / video_ids 置空。

    ⚠️ 置空是刻意的：`video.py` 用 `done = len(video_urls)` 做断点；
    置空后 done=0，逐段循环会走 `existing_video_url` 复用分支，从而按真实索引
    复用已完成的段（不连续索引也正确，因为复用是逐段按 idx 判断的）。
    同时必须清 `video_ids`——否则 `id_by_index` 会残留与 url 索引错位。

    返回写回的段数。
    """
    done = (progress or {}).get("done") or {}
    count = 0
    for key, entry in done.items():
        idx = _to_index(key)
        if idx is None:
            continue
        if isinstance(entry, dict):
            url = str(entry.get("url") or "").strip()
        else:
            url = str(entry or "").strip()
        if not url:
            continue
        if _set_reuse_url(state, idx, url):
            count += 1
    state["video_urls"] = []
    state["video_ids"] = []
    return count


def _model_name(state: dict) -> str:
    from app.config import settings

    return str(state.get("video_model") or "").strip() or settings.video_model_fast


async def _probe_video(video_id: str, model_name: str) -> tuple[dict | None, str]:
    """探测一个已提交的 video_id 状态。

    提交时用的 provider **没有持久化**（progress 契约只存 video_id），所以逐个
    provider 试一次；返回 (响应体或 None, 命中的 provider 名)。
    """
    try:
        names = list(getattr(gateway, "provider_names", None) or ["intl"])
    except Exception:
        names = ["intl"]
    fallback = names[0] if names else "intl"
    for name in names:
        try:
            res = await gateway.query_video(video_id, model_name, provider_name=name)
        except Exception as exc:
            logger.debug("query_video(%s, provider=%s) 失败: %s", video_id, name, exc)
            continue
        if isinstance(res, dict) and (
            res.get("status") or res.get("url") or res.get("video_url") or res.get("error")
        ):
            return res, name
    return None, fallback


async def resolve_submitted(sid: str, state: dict, progress: dict) -> int:
    """处理 submitted 但未 done 的 video_id：**不重新提交**，查状态后分流。

    返回「查回来确认完成」的段数。
    """
    submitted = (progress or {}).get("submitted") or {}
    done = (progress or {}).get("done") or {}
    if not submitted:
        return 0
    model_name = _model_name(state)
    resolved = 0
    for key, video_id in submitted.items():
        idx = _to_index(key)
        vid = str(video_id or "").strip()
        if idx is None or not vid:
            continue
        if str(idx) in done or key in done:
            continue
        res, provider = await _probe_video(vid, model_name)
        status = ""
        url = ""
        err = None
        if isinstance(res, dict):
            status = str(res.get("status") or res.get("internal_status") or "").lower()
            url = str(res.get("url") or res.get("video_url") or "").strip()
            err = res.get("error")

        if url and status in _DONE_STATUS:
            # 已完成 → 直接落 done（零成本，不重新生成）
            progress.setdefault("done", {})[str(idx)] = {"url": url, "id": vid}
            await session_store.mark_done(sid, idx, url, vid)
            resolved += 1
            logger.info("恢复：第 %d 段视频已在 agnes 完成，直接复用 %s", idx, url)
        elif status in _FAILED_STATUS or err:
            # 失败/过期 → 从 submitted 移除，留空交给正常重生成
            progress.setdefault("submitted", {}).pop(str(idx), None)
            await session_store.clear_submitted(sid, idx)
            logger.info("恢复：第 %d 段视频已失败/过期，交给正常重生成", idx)
        else:
            # 仍生成中（或查询结果不可判定，保守处理）→ 复用原 video_id 继续等
            _set_pending_video(state, idx, vid)
            try:
                await poller.submit(vid, model_name, sid, idx, provider=provider)
            except Exception as exc:  # pragma: no cover - poller 异常不该阻断恢复
                logger.warning("恢复：第 %d 段重新挂 poller 失败: %s", idx, exc)
            logger.info("恢复：第 %d 段视频仍在生成，复用 video_id=%s 继续等待", idx, vid)
    return resolved


# ------------------------------------------------------------------- 单会话恢复

async def recover_session(sid: str) -> bool:
    """恢复单个会话。返回是否已重新入队。任何异常都只 log。"""
    from app.main import _sessions  # 延迟导入避免循环依赖

    if sid in _recovered:
        return False
    if not await session_store.acquire_lock(sid):
        logger.info("会话 %s 的恢复锁被其它进程持有，跳过", sid)
        return False
    _recovered.add(sid)

    state = await session_store.load_state(sid)
    if not state:
        # 快照过期/丢失 → 清掉活跃索引，交给 Java 看门狗与重试器兜底
        logger.info("会话 %s 无可用快照，清除活跃索引", sid)
        await session_store.remove_active(sid)
        return False

    status = str(state.get("status") or "")
    if status in ("completed", "failed", "expired"):
        logger.info("会话 %s 已是终态(%s)，清理快照不做恢复", sid, status)
        await session_store.delete_session(sid)
        return False

    progress = await session_store.load_progress(sid)
    # 1) 在飞的提交先核实（可能顺手捞回已完成的，也可能转为 pending_video_id）
    await resolve_submitted(sid, state, progress)
    # 2) 已完成的按索引回填复用字段 + 清空 video_urls/video_ids
    reused = merge_done_into_state(state, progress)

    # 3) 先回填 _sessions 再入队（scheduler._run_one 从 _sessions 取 state）
    state["status"] = state.get("status") or "queued"
    _sessions[sid] = state
    scheduler.submit(sid)
    logger.info(
        "会话 %s 已恢复并重新入队（复用已完成 %d 段）", sid, reused
    )

    # 4) 通知 Java 重新武装看门狗（接口可能尚不存在 → notify 内部重试+fallback，静默降级）
    try:
        from app.callback.java_notify import notify_java_completion

        asyncio.create_task(
            notify_java_completion(session_id=sid, status="queued")
        )
    except Exception as exc:
        logger.debug("恢复后通知 Java 失败（忽略）: %s", exc)
    return True


async def recover_active_sessions() -> None:
    """启动恢复入口：遍历 `dw:agent:active` 逐个恢复。绝不抛异常。"""
    try:
        active = await session_store.list_active()
        if not active:
            logger.info("启动恢复：无活跃会话需要恢复")
            return
        logger.info("启动恢复：发现 %d 个活跃会话 %s", len(active), active)
        for sid in active:
            try:
                await recover_session(sid)
            except Exception as exc:
                logger.warning("会话 %s 恢复失败（忽略）: %s", sid, exc)
    except Exception as exc:  # 整体兜底：绝不阻塞服务启动
        logger.warning("启动恢复流程异常（忽略，不影响服务）: %s", exc)
