"""Redis 会话快照持久化（方案 C：显式快照 + 节点幂等守卫 + 提交即落 video_id）。

为什么不用 `langgraph-checkpoint-redis`：
- checkpointer 是「节点边界」粒度，救不了 `video_generator` 全段并发提交中途被杀的场景
  （已提交的 agnes `video_id` 一个都没进 state → 恢复时全部重新提交，额度白花两遍）
- 官方包还拖 `redisvl` 等约 6 个新依赖，且存的是 msgpack blob，排障不可读

硬性约束（最重要）：**Redis 不可用时所有方法必须静默降级为 no-op / None**，
绝不能因为持久化把主流程搞挂。所有对外方法内部都吞异常，只 log.debug。

Redis 键契约（agent 用 db=1，避免与 Java Redisson 的 `dw:task:watchdog` 混在 db0）：
    dw:agent:sess:{sid}             state 完整 JSON 字符串，TTL 24h
    dw:agent:sess:{sid}:progress    {"submitted":{idx:vid},"done":{idx:{url,id}}}，TTL 24h
    dw:agent:active                 Set，活跃 sid
    dw:agent:lock:{sid}             恢复互斥锁（SETNX + TTL 5min）

并发说明：progress 是读-改-写，但**不需要加锁**——同一会话内 `mark_submitted`
在 `video.py` 的提交循环里是顺序 await，`mark_done` 在 `asyncio.gather` 之后顺序执行，
两者不会交错；跨会话的 key 不同。故不引入 asyncio.Lock（还避免跨事件循环绑定问题）。
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

try:  # redis 未安装/导入失败 → 整体降级为纯内存行为
    import redis.asyncio as _aioredis
except Exception:  # pragma: no cover - 环境缺包时走降级路径
    _aioredis = None
    logger.warning("redis 包不可用，会话持久化降级为纯内存")

STATE_KEY = "dw:agent:sess:{sid}"
PROGRESS_KEY = "dw:agent:sess:{sid}:progress"
ACTIVE_KEY = "dw:agent:active"
LOCK_KEY = "dw:agent:lock:{sid}"

# 恢复互斥锁 TTL：5 分钟（约定值，不随业务增长）
LOCK_TTL_S = 300

_EMPTY_PROGRESS = {"submitted": {}, "done": {}}


class SessionStore:
    """Redis 会话存储封装。任何 Redis 异常都静默降级（主流程不受影响）。"""

    def __init__(self) -> None:
        self._client: Any = None
        # 单测可置 False 关闭持久化（避免污染真实 Redis / 网络抖动影响断言）
        self.enabled = True

    # ------------------------------------------------------------------ 内部

    def _get_client(self):
        """惰性建连：import 本模块不产生任何网络 IO。不可用时返回 None。"""
        if not self.enabled or _aioredis is None:
            return None
        if self._client is None:
            try:
                from app.config import settings

                # protocol=2 是硬要求：本机是 Redis 3.2.100（微软 Windows 移植版），
                # 不支持 HELLO 命令；redis-py 8.x 默认走 RESP3 握手会直接报
                # `URL unknown command 'HELLO'`。锁 RESP2 才能连上。
                self._client = _aioredis.from_url(
                    settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    protocol=2,
                )
            except Exception as exc:  # 配置非法等
                logger.debug("创建 Redis 客户端失败（持久化降级）: %s", exc)
                return None
        return self._client

    def _ttl(self) -> int:
        try:
            from app.config import settings

            return max(60, int(settings.session_snapshot_ttl_s))
        except Exception:
            return 86400

    # ------------------------------------------------------------------ 健康

    async def ping(self) -> bool:
        """连通性探测（返回 False 表示降级中）。"""
        client = self._get_client()
        if client is None:
            return False
        try:
            return bool(await client.ping())
        except Exception as exc:
            logger.debug("Redis ping 失败（持久化降级）: %s", exc)
            return False

    # ------------------------------------------------------- state 快照读写

    async def save_state(self, sid: str, state: dict) -> None:
        """写 state 完整快照（每个 checkpoint 覆盖写）。失败静默。"""
        client = self._get_client()
        if client is None or not sid:
            return
        try:
            payload = json.dumps(state, ensure_ascii=False, default=str)
            await client.set(STATE_KEY.format(sid=sid), payload, ex=self._ttl())
        except Exception as exc:
            logger.debug("save_state 失败（已降级）: %s", exc)

    async def load_state(self, sid: str) -> dict | None:
        """读 state 快照；不存在/损坏/Redis 不可用均返回 None。"""
        client = self._get_client()
        if client is None or not sid:
            return None
        try:
            raw = await client.get(STATE_KEY.format(sid=sid))
        except Exception as exc:
            logger.debug("load_state 失败（已降级）: %s", exc)
            return None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            logger.warning("会话 %s 的 state 快照不是合法 JSON，忽略", sid)
            return None

    async def delete_session(self, sid: str) -> None:
        """终态清理：DEL 两个 key + SREM active（失败静默）。"""
        client = self._get_client()
        if client is None or not sid:
            return
        try:
            await client.delete(STATE_KEY.format(sid=sid), PROGRESS_KEY.format(sid=sid))
            await client.srem(ACTIVE_KEY, sid)
        except Exception as exc:
            logger.debug("delete_session 失败（已降级）: %s", exc)

    # ------------------------------------------------------------ active 集合

    async def add_active(self, sid: str) -> None:
        client = self._get_client()
        if client is None or not sid:
            return
        try:
            await client.sadd(ACTIVE_KEY, sid)
        except Exception as exc:
            logger.debug("add_active 失败（已降级）: %s", exc)

    async def remove_active(self, sid: str) -> None:
        client = self._get_client()
        if client is None or not sid:
            return
        try:
            await client.srem(ACTIVE_KEY, sid)
        except Exception as exc:
            logger.debug("remove_active 失败（已降级）: %s", exc)

    async def list_active(self) -> list[str]:
        """活跃会话列表；Redis 不可用返回空列表（等价于无会话可恢复）。"""
        client = self._get_client()
        if client is None:
            return []
        try:
            members = await client.smembers(ACTIVE_KEY)
        except Exception as exc:
            logger.debug("list_active 失败（已降级）: %s", exc)
            return []
        if not members:
            return []
        return sorted(str(m) for m in members)

    # -------------------------------------------------------------- progress

    async def load_progress(self, sid: str) -> dict:
        """读 progress；结构恒为 {"submitted": {...}, "done": {...}}。"""
        client = self._get_client()
        if client is None or not sid:
            return {"submitted": {}, "done": {}}
        try:
            raw = await client.get(PROGRESS_KEY.format(sid=sid))
        except Exception as exc:
            logger.debug("load_progress 失败（已降级）: %s", exc)
            return {"submitted": {}, "done": {}}
        if not raw:
            return {"submitted": {}, "done": {}}
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("会话 %s 的 progress 不是合法 JSON，忽略", sid)
            return {"submitted": {}, "done": {}}
        if not isinstance(parsed, dict):
            return {"submitted": {}, "done": {}}
        submitted = parsed.get("submitted")
        done = parsed.get("done")
        return {
            "submitted": submitted if isinstance(submitted, dict) else {},
            "done": done if isinstance(done, dict) else {},
        }

    async def save_progress(self, sid: str, progress: dict) -> None:
        """覆盖写 progress 全量（失败静默）。"""
        client = self._get_client()
        if client is None or not sid:
            return
        payload = {
            "submitted": dict((progress or {}).get("submitted") or {}),
            "done": dict((progress or {}).get("done") or {}),
        }
        try:
            await client.set(
                PROGRESS_KEY.format(sid=sid),
                json.dumps(payload, ensure_ascii=False, default=str),
                ex=self._ttl(),
            )
        except Exception as exc:
            logger.debug("save_progress 失败（已降级）: %s", exc)

    async def mark_submitted(self, sid: str, shot_index: int, video_id: str) -> None:
        """提交成功即落盘 video_id（全方案最关键的一行调用）。

        进程若在此刻之后被杀，恢复时可用 `query_video(video_id)` 零成本取回结果，
        绝不重新提交、不重复烧 agnes 额度。
        """
        if not sid or not video_id:
            return
        progress = await self.load_progress(sid)
        progress["submitted"][str(int(shot_index))] = str(video_id)
        await self.save_progress(sid, progress)

    async def clear_submitted(self, sid: str, shot_index: int) -> None:
        """从 submitted 移除某段（该段已确认失败/过期 → 交给正常重生成）。"""
        if not sid:
            return
        progress = await self.load_progress(sid)
        progress["submitted"].pop(str(int(shot_index)), None)
        await self.save_progress(sid, progress)

    async def mark_done(self, sid: str, shot_index: int, url: str,
                        video_id: str = "") -> None:
        """某段视频确认完成（新生成或复用）→ 落 progress.done。失败静默。"""
        if not sid or not url:
            return
        idx = str(int(shot_index))
        progress = await self.load_progress(sid)
        progress["done"][idx] = {"url": str(url), "id": str(video_id or "")}
        progress["submitted"].pop(idx, None)
        await self.save_progress(sid, progress)

    # ------------------------------------------------------------ 恢复互斥锁

    async def acquire_lock(self, sid: str) -> bool:
        """抢恢复互斥锁（SETNX + TTL 5min）。抢不到返回 False。

        成功的恢复**不主动释放锁**，让 TTL 自然过期：`--reload` 下新旧进程可能重叠，
        旧进程若二次恢复同一会话 → 同一任务重复提交 agnes（正是本方案要避免的浪费）。
        ⚠️ 代价（已知，接受）：进程若在成功恢复后 5 分钟内**再崩一次**并重启，
        这次恢复会被自己的残留锁挡住。此时靠 Java 看门狗 + TaskAutoRetryer 兜底，
        比「牺牲额度换即时性」划算。
        "什么都没恢复" 的路径（快照丢失/已是终态）会显式 release_lock，不占用锁窗口。
        """
        client = self._get_client()
        if client is None or not sid:
            # Redis 不可用 → 无互斥可言，放行（降级为「本进程内恢复」）
            return True
        try:
            ok = await client.set(
                LOCK_KEY.format(sid=sid), "1", nx=True, ex=LOCK_TTL_S
            )
            return bool(ok)
        except Exception as exc:
            logger.debug("acquire_lock 失败（降级放行）: %s", exc)
            return True

    async def release_lock(self, sid: str) -> None:
        """释放恢复锁（仅用于「没有实际恢复」的路径）。失败静默。"""
        client = self._get_client()
        if client is None or not sid:
            return
        try:
            await client.delete(LOCK_KEY.format(sid=sid))
        except Exception as exc:
            logger.debug("release_lock 失败（已降级）: %s", exc)

    # ------------------------------------------------------------------ 收尾

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.aclose()
        except Exception:
            pass
        finally:
            self._client = None


# 模块级单例（与 poller / scheduler 同风格）
store = SessionStore()


# ---- 模块级薄封装：调用方统一走 session_store.xxx（不必层层 .store）----

async def ping() -> bool:
    return await store.ping()


async def save_state(sid: str, state: dict) -> None:
    await store.save_state(sid, state)


async def load_state(sid: str) -> dict | None:
    return await store.load_state(sid)


async def delete_session(sid: str) -> None:
    await store.delete_session(sid)


async def add_active(sid: str) -> None:
    await store.add_active(sid)


async def remove_active(sid: str) -> None:
    await store.remove_active(sid)


async def list_active() -> list[str]:
    return await store.list_active()


async def load_progress(sid: str) -> dict:
    return await store.load_progress(sid)


async def save_progress(sid: str, progress: dict) -> None:
    await store.save_progress(sid, progress)


async def mark_submitted(sid: str, shot_index: int, video_id: str) -> None:
    await store.mark_submitted(sid, shot_index, video_id)


async def clear_submitted(sid: str, shot_index: int) -> None:
    await store.clear_submitted(sid, shot_index)


async def mark_done(sid: str, shot_index: int, url: str, video_id: str = "") -> None:
    await store.mark_done(sid, shot_index, url, video_id)


async def acquire_lock(sid: str) -> bool:
    return await store.acquire_lock(sid)


async def release_lock(sid: str) -> None:
    await store.release_lock(sid)


async def close() -> None:
    await store.close()
