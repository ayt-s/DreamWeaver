# DreamWeaver — Agent 服务入口
"""FastAPI 入口。

- POST /v1/tasks/video  提交创作任务（进入调度队列，返回 session_id + 排队编号）
- POST /v1/tasks/{id}/cancel  取消排队中的会话（已开始执行则 409）
- GET  /v1/tasks/{id}   查询任务状态（含中间产物，前端轨迹展示用）
- GET  /v1/tasks/{id}/events  SSE 轨迹事件（Phase 1 简化：轮询状态接口兜底）
- GET  /v1/scheduler    调度队列快照（执行中 / 排队中，画廊排期展示用）
- /v1/files/*  本地产物静态目录（synthesizer 拼接的长视频等）

支持三种 gen_type：
- text_video  纯文本 → 视频
- image_video 图生视频（reference_images 单图参考 或 segments 无限画布多段拼接）
- text_image  文生图（只出图不出视频）

并发控制：SessionScheduler 有界并发（默认同时执行 2 个会话），
超出的会话进入 FIFO 队列排队，避免多个长任务同时压向 Agnes API 触发限流。
"""
import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import abort, events, session_store
from app.errors import AppError, friendly_error_message, register_exception_handlers
from app.graph import compiled_graph
from app.state import CreativeSessionState, TaskStatus
from app.utils.prompting import normalize_camera_spec, normalize_image_ratio
from app.poller import poller
from app.scheduler import QueueFullError, scheduler
from app.agent.chat_api import router as agent_chat_router
from app.controller.novel_api import router as novel_api_router
from app.controller.novel_anchors_api import router as novel_anchors_router
from app.controller.novel_recompose_api import router as novel_recompose_router
from app.controller.qc_api import router as qc_api_router
from app.controller.image_edit_api import router as image_edit_router
from app.controller.internal_api import router as internal_router

app = FastAPI(title="DreamWeaver Agent Service", version="0.2.0")
register_exception_handlers(app)

# 本地产物静态目录（与 app/utils/media.py 的 output_root() 对应）：
# /v1/files/<session>/final.mp4 → web-frontend vite 代理 /v1 → 8000，可直接 <video> 播放
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/v1/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")

# 聊天 Agent 路由（Phase 1）：POST /v1/agent/chat
app.include_router(agent_chat_router)

# 小说转漫剧预处理路由：POST /v1/novel/preprocess
app.include_router(novel_api_router)

# 小说角色/场景锚定图路由：POST /v1/novel/anchors
app.include_router(novel_anchors_router)

# 存量画布提示词重算：POST /v1/novel/recompose-prompts（纯函数，规则单一出处）
app.include_router(novel_recompose_router)

# 首帧质检路由：POST /v1/qc/images（面部特写判定，前端按需调用，不落库）
app.include_router(qc_api_router)

# 图像定点修正路由：POST /v1/images/edit（图生图改一处，前端按需调用，不落库）
app.include_router(image_edit_router)

# 内部同步端点（Java 侧启动时拉取本地 fallback 记录，防回调失败丢数据）
app.include_router(internal_router)

# 内存态会话仓（Phase 1）。生产换 PostgreSQL 落库（设计文档 §4.1）
_sessions: dict[str, CreativeSessionState] = {}


# 统一响应体（与 Java CommonResult 对齐：code/message/data）
class ApiResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Optional[dict] = None


class CreateVideoTaskRequest(BaseModel):
    prompt: str
    user_id: Optional[str] = "demo-user"
    # 生成类型：text_video(纯文本视频)/image_video(图生视频)/text_image(文生图)
    gen_type: Optional[str] = "text_video"
    # 画布/标准模式可选视频模型（空 = 用配置默认 agnes-video-2.5-flash）
    video_model: Optional[str] = None
    # 用户上传的参考图片 URL 数组（JSON 字符串，Java 侧原样透传）；空则文生图自动喂
    reference_images: Optional[str] = None
    # 无限画布图生视频：片段数组 JSON 字符串 [{image_url, prompt, seconds}]；
    # 每段一张参考图 + 一段视频内容描述，生成几秒小视频后由 synthesizer 拼接成长视频
    segments: Optional[str] = None
    # 图片合成视频：从已有图片直接拼成片（ffmpeg 幻灯片，不消耗 agnes 额度）
    slideshow_images: Optional[str] = None
    slide_seconds: Optional[float] = None
    # === 可灵式精细控制 ===
    # 全局风格提示词（折进每镜提示词正文）
    style_prompt: Optional[str] = None
    # 负面提示词（折成「避免出现：…」进正文）
    negative_prompt: Optional[str] = None
    # 时间轴：总时长（秒）/ 镜头数
    total_seconds: Optional[int] = None
    shot_count: Optional[int] = None
    # 全局运镜倾向（标准模式 LLM 自由分镜时用；画布模式用段级 camera_spec，不受此影响）
    # JSON 字符串：{"shot_size": "远景", "angle": "平视", "movement": "推近"}
    shot_language: Optional[str] = None
    # 元素语义绑定 JSON：[{name, image_index}]，image_index 1-based（<Picture N>）
    reference_bindings: Optional[str] = None
    # 直出图（画布节点「一键文生图」）：跳过需求解析/剧本/分镜，直接按 prompt 出图。
    # 不设这个开关的话，「一镜一 prompt」会被 LLM 重新拆镜 → 一次任务产出多张
    # 不同画面的图（实测 5 张 / 3 张），前端只用得上第 1 张，其余白花额度。
    direct_image: Optional[bool] = False
    # 直出图候选张数（1~5）；同一 prompt 多次请求，产出多个候选供人选一张
    image_count: Optional[int] = 1
    # 出图画幅（如 "16:9" / "9:16"）。**必须显式传** —— 不传时服务端按 1:1 出正方形：
    # 2026-09-18 实测项目真实产物 18/18 都是 1024x1024，而视频链路是 16:9。
    image_ratio: Optional[str] = None
    # 首帧锁定（keyframe，默认开）：有首帧图时把它当视频的**实际第一帧**，
    # 而不是塞进 images 参考数组（reference 模式官方明确「可能重新构图、重新计时」）
    lock_first_frame: Optional[bool] = None
    # 段间衔接（默认关）：把下一段的首帧当本段尾帧，让相邻段首尾接得上
    chain_frames: Optional[bool] = None


class CreateVideoTaskResponse(BaseModel):
    session_id: str
    status: str


def _parse_json_list(raw: str | None, name: str) -> list:
    """解析 Java 侧透传的 JSON 数组字符串；非法则返回空列表。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        logger.warning("%s 不是合法 JSON 数组: %s", name, raw[:100])
    return []


def _parse_json_obj(raw: str | None, name: str) -> dict:
    """解析 Java 侧透传的 JSON 对象字符串；非法则返回空 dict。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        logger.warning("%s 不是合法 JSON 对象: %s", name, raw[:100])
    return {}


def _parse_segments(raw: str | None) -> list:
    """解析无限画布片段 JSON：[] -> [{image_url, prompt, seconds, reference_images, existing_video_url, prompt_en}]。

    保留两类 segment：
    1. 带 image_url 的（用户单张图）
    2. 带 reference_images 数组的（用户多图/锚定图）
    两者都无则丢弃。

    prompt_en 是预翻译的英文提示词（段重生时 storyboard 已带），有则跳过 LLM 翻译。
    cn_description 是中文描述，storyboard 格式用此字段代替 prompt。
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        segments = []
        for s in parsed:
            if not isinstance(s, dict):
                continue
            image_url = str(s.get("image_url", "")).strip()
            # 兼容用户提供多参考图（锚定图等）
            ref_images = s.get("reference_images") or []
            if not image_url and not ref_images:
                continue  # 既无单图也无多图，丢弃
            try:
                seconds = int(s.get("seconds", 5) or 5)
            except (TypeError, ValueError):
                seconds = 5
            # prompt 来源优先级：prompt > cn_description（storyboard 格式）
            prompt = str(s.get("prompt", "")).strip()
            if not prompt:
                prompt = str(s.get("cn_description", "")).strip()
            segments.append({
                "image_url": image_url,
                "reference_images": list(ref_images),
                "prompt": prompt,
                "seconds": seconds,
                "aspect_ratio": str(s.get("aspect_ratio") or "16:9").strip(),
                # 重生混合模式：该段已有视频 URL → 直接复用，跳过重新生成
                "existing_video_url": str(s.get("existing_video_url") or "").strip(),
                # 预翻译英文提示词（段重生时 storyboard 已带，跳过 LLM 翻译）
                "prompt_en": str(s.get("prompt_en", "")).strip(),
                # 该段已有图片 URL（图片任务段重生复用）
                "existing_image_url": str(s.get("existing_image_url") or "").strip(),
                # 可灵式结构化运镜：{shot_size, angle, movement}
                "camera_spec": normalize_camera_spec(s.get("camera_spec") or s.get("camera")),
                # 该段级负面词（覆盖全局）
                "negative_prompt": str(s.get("negative_prompt") or "").strip(),
            })
        return segments
    except json.JSONDecodeError:
        logger.warning("segments 不是合法 JSON 数组: %s", raw[:100])
        return []


def _release_session(session_id: str) -> None:
    """会话保留期（1 小时）结束：释放内存态 + 事件总线。

    两样都要放，否则长期运行会无界增长：
    - `_sessions`：每会话一份完整 state（含 storyboard / video_urls / trace）
    - `events` 总线：每会话一个 `deque(maxlen=500)` 事件缓冲

    保留期设为 1 小时是有意的：任务结束后用户仍会回看轨迹（含 SSE 重放），
    过期才释放。

    ⚠️ 本函数**只该由 `_schedule_session_release` 调用**：定时器句柄必须与
    会话同生共死（见那边的注释）。
    """
    _sessions.pop(session_id, None)
    asyncio.get_running_loop().create_task(events.clear(session_id))
    logger.debug("会话 %s 保留期结束，已释放内存态与事件总线", session_id)


# ★ 2026-09-19 修（#4）：保留期定时器必须**可重挂 + 可撤销**。
#
# 改前形状（两条独立的漏洞，合起来让「保留期」形同虚设）：
#   1. `_run_session` 收尾时 `call_later(3600, _release_session, sid)` 挂一次定时器，
#      此后再没有任何路径重新挂 —— 连 `_release_session` 自己都只是 `pop`，不安排下一次。
#   2. `get_task` 内存未命中回落 Redis 快照后会把整份 state **回填** `_sessions[sid]`
#      （main.py:666 附近，注释写的理由是「前端每 3s 轮询，不回填就会反复打 Redis」）。
#      前端 TrajectoryPanel 恰好是 3s 一次，且**完成/失败后仍会继续轮询**。
#   后果链（真实可达，非理论）：任务完成 → 1 小时后 _release_session 把 state 与事件总线
#   都释放 → 用户还开着轨迹面板，下一次轮询（3s 内）从 Redis 快照回填 → 整份 state
#   （storyboard + trace + 逐镜质检明细）**永久回到内存**，且**没有任何定时器**会再释放它。
#   进程只要不重启就一直在，恰恰是注释里想避免的「无界增长」。
#   还有一处对称的浪费：内存命中时本不该碰 Redis，可回填后内存态永远不会被清，
#   于是轮询变成「内存命中」——看似变快了，代价是把释放彻底废掉。
#
# 改法：所有回填点统一走 `_schedule_session_release`，它
#   (a) 撤销该会话可能存在的旧定时器（避免重复/提前释放），
#   (b) 重新挂一个 1 小时的释放定时器，
# 即「只要有人（重新）看到它，保留期就重新计时」；没人看则 1 小时后照旧释放。
_release_timers: dict[str, asyncio.TimerHandle] = {}


def _schedule_session_release(session_id: str, delay_s: float = 3600.0) -> None:
    """安排（或重排）会话内存态与事件总线的释放。

    幂等：重复调用只是把释放时间往后推（撤销旧句柄再挂新的），
    不会叠加出多个定时器，也不会提前释放。
    """
    if not session_id:
        return
    old = _release_timers.pop(session_id, None)
    if old is not None:
        old.cancel()
    try:
        handle = asyncio.get_running_loop().call_later(
            delay_s, _release_session, session_id)
    except RuntimeError:  # 无运行中的事件循环（同步调用/测试收尾）：不做定时
        return
    _release_timers[session_id] = handle


def _cancel_scheduled_release(session_id: str) -> None:
    """撤销会话的释放定时器（会话已彻底消失时调用，避免句柄表无界增长）。

    调用场景：`cancel` 已把快照与活跃索引都删掉、用户已无法再查到该会话 ——
    此时再留一个 1 小时后会 no-op 的定时器只是句柄泄漏。
    """
    old = _release_timers.pop(session_id, None)
    if old is not None:
        old.cancel()


async def _run_session(state: CreativeSessionState) -> None:
    """后台执行 LangGraph。由 SessionScheduler 调用（有界并发）。"""
    config = {"configurable": {"thread_id": state["session_id"]}}
    # 心跳续期：独立于节点边界——video_generator 单节点可能跑 15 分钟以上
    hb_task = asyncio.create_task(_heartbeat_loop(state["session_id"]))
    # 「已跑到终态」标志：正常结束 / 已按失败落定 → True（可清快照）；
    # 被取消（CancelledError 不被下面的 except Exception 捕获）→ 保持 False（保留快照待恢复）
    settled = False
    try:
        # 用 astream 替代 ainvoke：每个 checkpoint 实时同步 state，
        # 让 /v1/tasks/{sid} 能反映执行进度（video_generating/synthesizing 等），
        # 而不是等会话结束后才一次性更新——修复「提交后状态永远 queued、用户以为没执行」的问题。
        # 最后一个 checkpoint 等价于 ainvoke 的返回值，语义不变。
        result = None
        async for new_state in compiled_graph.astream(state, config=config, stream_mode="values"):
            if new_state:
                result = new_state
                sid = new_state.get("session_id") or state.get("session_id")
                if sid:
                    prev_status = state.get("status")
                    state["status"] = new_state.get("status")
                    state["updated_at"] = int(time.time())
                    # 会话视图指向最新累积态（stream_mode="values"），
                    # 查询接口才能看到 brief/script/storyboard/视频产物与实时状态
                    _sessions[sid] = new_state
                    # 会话持久化：每个 checkpoint 覆盖写 state 快照
                    # （Redis 挂则静默降级，绝不影响主流程）
                    await session_store.save_state(sid, new_state)
                    if prev_status != new_state.get("status"):
                        logger.info("会话 %s 状态 %s → %s", sid, prev_status, new_state.get("status"))
        if result is None:
            result = state
        _sessions[state["session_id"]] = result
        # 轨迹完成事件（节点可能已发过 completed，重复无害）
        await events.emit(state["session_id"], "completed", {})
        # 会话保留 1 小时用于查轨迹，之后释放，防止 _sessions / _buses 无界增长
        # ★ 2026-09-19 修（#4）：走可重挂的定时器（见 _schedule_session_release）
        _schedule_session_release(state["session_id"])
        settled = True
    except Exception as exc:  # 节点异常 → 记 FAILED，不裸崩后台任务
        # 异常 str() 可能为空（如部分 asyncio 异常），兜底用异常类型名；
        # 用户侧文案友好化，完整异常只进日志
        msg = friendly_error_message(exc)
        logger.error(f"Session {state['session_id']} failed", exc_info=exc)
        # ★ 2026-09-19 修（#3）：失败态必须建立在**已累积的最新 state** 上。
        #   stream 每帧都会把 _sessions[sid] 更新成累积态（含 storyboard/trace/qc_report），
        #   而这里原来直接用**最初的输入 state** 赋值 → GET /v1/tasks/{sid} 立刻丢掉
        #   分镜/轨迹/质检明细 —— 而"哪一镜为什么失败"恰恰是失败时最需要看的信息。
        #   更糟的是重启后从快照拿回的是旧累积态，同一会话出现两种口径
        #   （内存=空、Redis=有），排障时必然误判。改为：以累积态为底，只覆盖终态字段。
        _accumulated = _sessions.get(state["session_id"])
        if isinstance(_accumulated, dict) and _accumulated.get("session_id") == state["session_id"]:
            state = {**_accumulated}
        state["status"] = TaskStatus.FAILED
        state["error_message"] = msg
        state["updated_at"] = int(time.time())
        # ★ 2026-09-19 修（#1·#9）：**失败态必须写进 Redis 快照**。
        #   原样只写 _sessions（内存）—— 而 _sessions 1 小时后就被 _release_session 释放，
        #   之后 GET /v1/tasks/{sid} 只能读快照，快照却停在最后一个 checkpoint 的状态：
        #   实测 DB 里 failed 的会话，agent 侧一直报 storyboard_writing / asset_generating。
        #   危害有二：① 前端轨迹面板恰好在失败时看不到失败原因与逐镜明细；
        #   ② recovery 的「终态守卫」按快照 status 判断，拿不到终态时会**把已失败的会话
        #   从头重跑**（白烧额度）。所以这里与每个 checkpoint 的持久化口径对齐。
        await session_store.save_state(state["session_id"], state)
        _sessions[state["session_id"]] = state
        await events.emit(state["session_id"], "failed", {"error": msg})
        # Phase 2 回调通知失败状态
        from app.callback.java_notify import notify_java_completion
        asyncio.create_task(
            notify_java_completion(
                session_id=state["session_id"],
                status=TaskStatus.FAILED,
                error_message=msg,
            )
        )
        # 不 re-raise：避免 "Task exception was never retrieved" 日志污染
        # 会话保留 1 小时用于查轨迹，之后释放，防止 _sessions / _buses 无界增长
        # ★ 2026-09-19 修（#4）：与成功分支同一口径（可重挂定时器）
        _schedule_session_release(state["session_id"])
        settled = True
    finally:
        # 会话收尾：无论成功/失败/被取消都先停心跳。
        hb_task.cancel()
        try:
            await hb_task  # await 一次收尾，避免残留 pending task 告警
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # 心跳自身的问题绝不影响会话结果
            logger.debug("心跳协程收尾异常（忽略）: %s", exc)
        # 只有「已跑到终态」才清 Redis 快照（静默降级由 session_store 保证）。
        # 被取消时 settled 仍为 False → 保留快照，下次启动继续恢复。
        # ⚠️ CancelledError 继承自 BaseException，上面的 except Exception 抓不到它，
        #    所以「取消」与「失败」必须靠 settled 区分：scheduler.stop() → w.cancel()
        #    正是 Ctrl+C 优雅停止的路径。若无条件清快照，就会变成「优雅停止丢会话、
        #    硬杀反而能恢复」（硬杀不执行 finally），与设计意图正好相反。
        abort.clear(state["session_id"])
        if settled:
            # 跑到终态：退出「活跃索引」+ 清 progress，但**保留 state 快照**（2026-09-17 改）。
            # 原先连快照一起删，导致任务一完成 `GET /v1/tasks/{sid}` 立刻 404 ——
            # 前端轨迹面板拿不到 trace、逐镜质检明细一并消失，而「哪一镜为什么没通过」
            # 恰恰是完成态最需要看的信息。
            # 安全前提：启动恢复按 `dw:agent:active` 集合筛（不是按有没有快照），
            # 这里已把 sid 移出，重启不会把它当活跃会话重跑。快照靠 TTL 自然过期。
            await session_store.settle_session(state["session_id"])


async def _heartbeat_loop(session_id: str) -> None:
    """心跳续期：每 heartbeat_interval_s 秒 POST 一次 /internal/heartbeat。

    目的：让 Java 侧重武装看门狗 TTL，把「固定截止时间」变成「空闲超时」
    （否则真跑超 30 分钟的长任务会被看门狗误杀）。

    硬性要求：Java 接口可能还不存在（并行实现中），**失败必须静默降级**
    （只 log.debug），绝不能让心跳把主流程搞挂。会话结束时由 _run_session cancel。
    """
    try:
        from app.config import settings

        base = (settings.java_notify_url or "").strip().rstrip("/")
        if not base:
            return
        interval = float(settings.heartbeat_interval_s or 0)
        if interval <= 0:  # 配 0 即关闭心跳
            return
        url = f"{base}/internal/heartbeat"
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                await asyncio.sleep(interval)
                try:
                    resp = await client.post(url, json={"session_id": session_id})
                    # Java 明确回 tracked=false = 该会话已无人认领
                    # （任务被「全量重生」换了 session_id，或已被删除/已终态）
                    # → 置中止位，各节点在花钱的提交点前会检查，避免白烧额度。
                    # 老版本 Java 返回空体 / 服务不可达 → 解析失败 → 不置位（保守）。
                    if resp.status_code == 200:
                        try:
                            data = (resp.json() or {}).get("data") or {}
                        except Exception:
                            data = {}
                        if data.get("tracked") is False:
                            logger.warning(
                                "会话 %s 在 Java 侧已无人认领（任务被重新生成/删除），标记中止",
                                session_id)
                            abort.mark(session_id)
                except Exception as exc:  # 静默降级：接口不存在/网络抖动都无所谓
                    logger.debug("心跳发送失败（忽略）session=%s: %s", session_id, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 兜底：心跳协程自身绝不影响会话
        logger.debug("心跳协程异常退出（忽略）session=%s: %s", session_id, exc)


@app.on_event("startup")
async def _startup() -> None:
    # 先挂文件日志再做别的：启动过程中的异常也要能事后回溯。
    # （stdout 日志会随手动重启消失，见 app/logging_setup.py 的模块注释）
    from app.config import settings
    from app.logging_setup import setup_file_logging

    log_path = setup_file_logging()
    if log_path:
        logger.info("文件日志已启用: %s（级别 %s，10MB × 5 份轮转）",
                    log_path, settings.log_level)

    await poller.start()
    await scheduler.start(runner=_run_session)
    # 启动自动恢复：把上次进程被杀时未完成的会话从 Redis 快照里捡回来断点续跑。
    # 用 create_task 异步执行——恢复流程要读 Redis、可能还要查 agnes，
    # 绝不能阻塞服务启动；内部已整体 try/except，失败只 log。
    from app.recovery import recover_active_sessions

    asyncio.create_task(recover_active_sessions())


@app.post("/v1/tasks/video", status_code=202, response_model=ApiResponse)
async def create_video_task(req: CreateVideoTaskRequest) -> ApiResponse:
    from app.config import settings

    if not req.prompt.strip():
        raise AppError("prompt 不能为空", status_code=422)

    session_id = uuid.uuid4().hex[:12]

    # ★ 2026-09-19 修（#5）：队列满的**预检口径**必须与闸门同源。
    #   改前形状：这里读 `scheduler.snapshot()["queued_count"]`（按 `_pending` 镜像数、
    #   且**排除已软取消的项**），而真正的闸门是 `asyncio.Queue(maxsize=...)` 的
    #   `put_nowait`（scheduler.py:35）。两个数字不同源：队列里还留着「排队期被取消、
    #   worker 尚未取走」的残留项，以及「worker 已取走但还没 task_done」的项 ——
    #   镜像都会少算。于是预检放行、`put_nowait` 抛 `asyncio.QueueFull`，
    #   一路冒到 FastAPI 就是 **500「服务器开小差了，请稍后重试」**（而非 429），
    #   用户与 Java 重试器都拿不到「该退避重试」的语义。
    #   改法：预检与闸门统一用 `is_queue_full()`（= `asyncio.Queue.qsize() >= maxsize`）。
    if scheduler.is_queue_full():
        raise AppError("任务队列已满，请稍后再试", status_code=429, retryable=True)

    ref_images = _parse_json_list(req.reference_images, "reference_images")
    segments = _parse_segments(req.segments)
    slideshow_images = _parse_json_list(req.slideshow_images, "slideshow_images")
    state: CreativeSessionState = {
        "session_id": session_id,
        "user_id": req.user_id or "demo-user",
        "raw_prompt": req.prompt,
        "gen_type": req.gen_type or "text_video",
        "reference_images": ref_images,
        "segments": segments,
        "slideshow": bool(slideshow_images),
        "slideshow_images": slideshow_images,
        "slide_seconds": req.slide_seconds or 3.0,
        # 可灵式精细控制：风格/负面词/时间轴/元素绑定
        "style_prompt": (req.style_prompt or "").strip(),
        "negative_prompt": (req.negative_prompt or "").strip(),
        "total_seconds": req.total_seconds,
        "shot_count": req.shot_count,
        # 全局运镜倾向：白名单清洗（防脏值进提示词）
        "shot_language": normalize_camera_spec(_parse_json_obj(req.shot_language, "shot_language")),
        "reference_bindings": _parse_json_list(req.reference_bindings, "reference_bindings"),
        # 直出图：跳过流水线直接出图；候选张数夹紧到 1~5
        "direct_image": bool(req.direct_image),
        "image_count": max(1, min(5, int(req.image_count or 1))),
        # 出图画幅：脏值一律回落默认（agnes 对非法取值直接 400）
        "image_ratio": normalize_image_ratio(req.image_ratio, settings.default_aspect_ratio),
        # 首帧锁定：None（未传）按开 —— 有首帧图时这才是「视频从这张图长出来」的路
        "lock_first_frame": True if req.lock_first_frame is None else bool(req.lock_first_frame),
        "chain_frames": bool(req.chain_frames),
        "status": TaskStatus.QUEUED,
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
        "trace": [],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    _sessions[session_id] = state
    # 会话持久化：写 state 快照 + 标记活跃（进程重启后可据此恢复）
    # Redis 不可用时静默降级，绝不影响任务提交
    await session_store.save_state(session_id, state)
    await session_store.add_active(session_id)
    # ★ 2026-09-19 修（#5）：入队与上面两步之间的**竞态**也必须收口。
    #   `put_nowait` 是唯一可能抛的闸门，且它发生在 state 已落盘/已标活跃之后 ——
    #   若在这里抛 QueueFull 而不管，Redis 里就留下一个「活跃但永不执行」的僵尸会话：
    #   启动恢复会把它捡回来重跑（白烧额度），GET /v1/tasks/{sid} 也能查到它。
    #   所以这里必须回滚（删快照 + 摘活跃索引），并把异常翻成 429 + retryable。
    try:
        position = scheduler.submit(session_id)
    except QueueFullError as exc:
        _sessions.pop(session_id, None)
        await session_store.delete_session(session_id)
        logger.warning("会话 %s 入队失败（队列满），已回滚会话态: %s", session_id, exc)
        raise AppError("任务队列已满，请稍后再试", status_code=429, retryable=True) from exc
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "session_id": session_id,
            "status": TaskStatus.QUEUED.value,
            "queue_position": position,
        }
    )


@app.post("/v1/tasks/{session_id}/cancel")
async def cancel_task(session_id: str) -> ApiResponse:
    """取消排队中的会话。已开始执行则返回 409（不非法打断运行中任务）。"""
    if session_id not in _sessions:
        raise AppError("session 不存在", status_code=404)
    if scheduler.cancel(session_id):
        _sessions[session_id]["status"] = TaskStatus.FAILED
        _sessions[session_id]["error_message"] = "用户取消排队"
        # ★ 2026-09-19 修（#4）：cancel 路径此前**从不释放**，是第二个漏洞。
        #   改前形状：只改状态 + 删 Redis 快照，`_sessions[sid]` 与 events 总线
        #   都留着（与 `_release_session` 的注释「两样都要放」自相矛盾），
        #   而保留期定时器只在会话**跑完**时才会挂 —— 取消发生在排队期，
        #   那条路径根本没挂过定时器 ⇒ 该会话的 state + 事件缓冲**永久驻留**进程。
        #   改法：补挂保留期定时器（与「跑完」同一口径，1 小时后释放内存态 + 事件总线）。
        #   ⚠️ 刻意不在这里立即释放：取消后前端仍会轮询一次 `GET /v1/tasks/{sid}`
        #   把卡片刷成「已取消」，而 Redis 快照已随取消删除 —— 立刻释放会让它变成 404，
        #   卡片停留在「排队中」。所以保留 1 小时可查，到期由定时器释放。
        _schedule_session_release(session_id)
        # 已取消 = 永远不会执行，清掉 Redis 快照与活跃索引，
        # 否则下次启动恢复会把这个用户已经取消的任务重新捡起来跑
        await session_store.delete_session(session_id)
        return ApiResponse(
            code=0,
            message="ok",
            data={"session_id": session_id, "canceled": True},
        )
    raise AppError("会话已开始执行，无法取消", status_code=409)


@app.get("/v1/scheduler")
async def scheduler_snapshot() -> ApiResponse:
    """调度队列快照：执行中 + 排队中的 session 列表（画廊排期展示）。"""
    return ApiResponse(code=0, message="ok", data=scheduler.snapshot())


@app.get("/v1/tasks/{session_id}/events")
async def task_events(session_id: str, request: Request):
    """SSE 轨迹事件流：节点实时发射 node/tool/progress 事件。

    **支持断线重连补漏**（F2）：浏览器 EventSource 重连时会自动带上
    `Last-Event-ID` 请求头，据此把断连期间错过的事件补发，再接上实时流。
    （也接受 `?last_event_id=` 便于 curl / 测试手动指定。）
    """
    last_event_id = _parse_last_event_id(
        request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    )
    replay, bus = await events.subscribe(session_id, last_event_id)

    async def generator():
        try:
            # 1) 先补历史（首次订阅时为空，因为「没人看就不缓冲」）
            cursor = last_event_id or 0
            for event in replay:
                cursor = max(cursor, event["event_id"])
                yield events.sse_format(event)

            # 2) 再等后续新事件。⚠️ yield 必须在锁外 ——
            #    在 cond 里 yield 会把 emit() 的 notify 卡住（客户端慢 = 节点写日志被阻塞）
            while True:
                async with bus.cond:
                    pending = bus.since(cursor)
                    if not pending:
                        try:
                            await asyncio.wait_for(
                                bus.cond.wait(), timeout=events.HEARTBEAT_SECONDS)
                        except asyncio.TimeoutError:
                            pass
                        pending = bus.since(cursor)
                if not pending:
                    yield ": heartbeat\n\n"   # 注释行，防止代理超时断连
                    continue
                for event in pending:
                    cursor = event["event_id"]
                    yield events.sse_format(event)
        finally:
            # 只减消费者计数，**不销毁缓冲** —— 否则刷新页面就没法补漏了
            await events.unsubscribe(session_id)

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.post("/v1/tasks/{session_id}/concat", response_model=ApiResponse)
async def concat_task_videos(session_id: str, force: bool = False) -> ApiResponse:
    """把已生成的分段视频拼接成一条成片（标准模式补上人工拼接入口）。

    背景：标准模式（无 segments）产出的是 N 个分段 URL，画廊只平铺展示；
    拼接能力此前只服务于画布模式的自动流程（synthesizer）与图片合成视频
    （image_slideshow），用户拿不到「把我这几段拼成一条」的入口。

    复用同一套本地工具：分段已由 asset_fetch 落在 `data/outputs/<sid>/seg_*.mp4`，
    直接 xfade 拼接；本地缺失时按 state.video_urls 下载兜底（老会话/目录被清理）。
    幂等：final.mp4 已存在且不早于最后一个分段 → 直接返回，不重复编码；
    **`?force=true` 则强制重拼** —— 拼接算法本身会修（例如 2026-09-18 修掉
    「多段成片整条没声音」），幂等短路会让既有成片永远拿不到修复。
    不消耗 agnes 额度（纯本地 ffmpeg）。

    实现已抽到 `app.utils.stitch`：标准模式的自动拼接（notify_final）与本端点共用同一条
    代码路径，避免两处各自演化。
    """
    from app.utils.stitch import stitch_session

    state = _sessions.get(session_id)
    if not state:
        state = (await session_store.load_state(session_id)) or {}

    try:
        result = await stitch_session(session_id, state.get("video_urls") or [], force=force)
    except RuntimeError as exc:
        # 拼接失败（源分段损坏 / 编码未成功）→ 给**可诊断**的中文错误。
        # 不接的话会被全局处理器吞成「服务器开小差了，请稍后重试」，用户与日志都看不出原因。
        # （实测触发场景：data/outputs 下早期 e2e 测试留下的 1KB 占位段）
        raise AppError(f"拼接失败：{exc}；请检查分段文件是否完整", status_code=502) from exc
    if result is None:
        raise AppError("可拼接的分段不足 2 个", status_code=409)

    return ApiResponse(code=0, message="ok", data={"session_id": session_id, **result})


class TextGenerateRequest(BaseModel):
    """画布文本节点的「AI 生成/改写」请求。"""

    instruction: str = Field(default="", description="用户意图，如「扩写成画面提示词」")
    context: str = Field(default="", description="现有文本（可空，作为改写对象）")


@app.post("/v1/text/generate", response_model=ApiResponse)
async def text_generate(req: TextGenerateRequest) -> ApiResponse:
    """单轮文本生成：给画布文本节点补内容 / 改写内容。

    与 `/v1/agent/chat` 的区别：**不**走对话循环、不挂画布工具，一次 LLM 调用返回纯文本。
    场景是「在节点里点一下就出一段提示词」这种高频轻操作 —— 走 chat 会带上工具循环和历史，
    又慢又容易返回寒暄。
    """
    from app.config import settings
    from app.gateway.agnes import gateway

    instruction = (req.instruction or "").strip()
    context = (req.context or "").strip()
    if not instruction and not context:
        raise AppError("instruction 与 context 不能同时为空", status_code=422)

    system = (
        "你是短视频分镜的画面描述助手。根据用户意图输出一段可直接用于 AI 生成画面的中文提示词。"
        "只输出提示词正文：不要解释、不要引号、不要 markdown、不要分点列举。"
        "100~200 字，包含主体 + 动作 + 场景 + 光线氛围 + 镜头感；"
        "不要写文字/水印/畸变类负面描述（负面词另走 negative_prompt）。"
    )
    user_msg = (
        f"用户意图：{instruction or '把下面的内容改写成更适合 AI 生成画面的提示词'}\n"
        f"现有内容：{context or '（空，请新写）'}"
    )
    try:
        raw = await gateway.chat(f"{system}\n\n{user_msg}",
                                 model=settings.text_model, temperature=0.7)
    except Exception as exc:
        logger.error("文本生成失败: %s", exc, exc_info=True)
        raise AppError("文本生成失败，请稍后重试", status_code=500, retryable=True)

    text = (raw or "").strip()
    if not text:
        raise AppError("文本模型返回空内容", status_code=500, retryable=True)
    return ApiResponse(code=0, message="ok", data={"text": text})


def _parse_last_event_id(raw: str | None) -> int | None:
    """解析 `Last-Event-ID`。非数字/缺失一律当「从头开始」。"""
    if not raw:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


@app.get("/v1/tasks/{session_id}", response_model=ApiResponse)
async def get_task(session_id: str) -> ApiResponse:
    """查询会话状态。

    **内存未命中时回落 Redis 快照**（F1）。

    能触发这条回落的场景：

    - 「**正常跑完**」的任务：快照**保留到 TTL**（2026-09-17 起，`settle_session`
      只退出活跃索引、不再删快照）→ 完成后的 `trace` 与逐镜质检明细**仍可查**，
      这正是前端轨迹面板需要的东西。语义变更原因见 `SessionStore.settle_session`。
    - 「**被杀 / 被取消**」的会话：快照同样保留（给 recovery 用），
      且**进程重启后 `_sessions` 里从来没有它** —— 这是 F1 最初修好的场景。

    命中快照时顺手回填内存：前端 `TrajectoryPanel` 每 3s 轮询，不回填就会反复打 Redis。
    """
    state = _sessions.get(session_id)
    if not state:
        snapshot = await session_store.load_state(session_id)
        if not snapshot:
            raise AppError("session 不存在", status_code=404)
        state = snapshot
        # 回填内存：前端轮询很密（3s 一次），不回填就会每次都打 Redis
        _sessions[session_id] = state
        # ★ 2026-09-19 修（#4）：回填**必须同时重挂保留期定时器**，否则这次回填
        #   就是永久驻留 —— 内存命中后 `_release_session` 再也不会被安排，
        #   state 与事件总线一起泄漏到进程结束（正是 _release_session 注释里
        #   要避免的「无界增长」）。前端轨迹面板在任务终态后仍以 3s 轮询，
        #   所以「释放后 3s 内又被回填」是必然路径，不是边界情况。
        _schedule_session_release(session_id)
        logger.debug("会话 %s 内存未命中，已从 Redis 快照回填（保留期重新计时）", session_id)
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "session_id": state["session_id"],
            "status": state.get("status"),
            "brief": state.get("brief"),
            "script": state.get("script"),
            "storyboard": state.get("storyboard"),
            "video_urls": state.get("video_urls"),
            "final_video_url": state.get("final_video_url"),
            "image_urls": state.get("image_urls"),
            "error_message": state.get("error_message"),
            # 质检报告（A7）：逐镜结果 {passed, total_shots, failed_shots, shots[], reason}
            # 前端 TrajectoryPanel 按 3s 轮询本接口，据此渲染逐镜质检明细。
            # 画廊卡片上的「N/M 镜未通过质检」走 Java 的 error_message
            # （notify_final 已把汇总写进去），所以这里只服务轨迹面板的明细展示。
            "qc_report": state.get("qc_report"),
            # 极简轨迹（批次 C3）：`[{node, status, elapsed_ms}]`。
            # TrajectoryPanel 现在只靠 SSE 画进度，SSE 断线/刷新页面后一片空白；
            # 有这份快照就能一直画出「节点 + 状态 + 耗时」时间线（顺带缓解 F 的丢事件）。
            "trace": state.get("trace") or [],
        }
    )


def _queue_maxsize() -> int:
    from app.config import settings
    return settings.session_queue_maxsize


@app.on_event("shutdown")
async def _shutdown() -> None:
    # ★ 2026-09-19 修（#1）：停机顺序决定「会话能不能续跑」。
    #   改前顺序：`ag.close()` → `poller.stop()` → `scheduler.stop()`。
    #   两个问题：
    #   (a) **先关网关**：还在飞的长任务（video_generator 可能跑 15 分钟以上）下一轮
    #       心跳/轮询会撞上已关闭的 httpx client，异常路径比「安静地交给重启」更脏；
    #   (b) `poller.stop()` 在 `scheduler.stop()` **之前**：poller 先把在途 future
    #       判成 TimeoutError（旧实现），而此刻会话任务还活着 —— 它会把失败当结果收下、
    #       走 notify_final 回调 Java 落成终态（链条见 poller.stop 的注释）。
    #   现在的顺序：先停调度器（worker 被 cancel → `_run_session` 的 settled 保持 False
    #   → 快照保留、不发终态回调），再停 poller（在途 future 保留未决），最后才关网关。
    #   结果：Ctrl+C 后 Java 侧任务停在非终态 → 看门狗转 interrupted → 重启 recovery 续跑。
    await scheduler.stop()
    await poller.stop()
    from app.gateway.agnes import gateway as ag
    await ag.close()
    await session_store.close()