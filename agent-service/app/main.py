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

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import abort, events, session_store
from app.errors import AppError, friendly_error_message, register_exception_handlers
from app.graph import compiled_graph
from app.state import CreativeSessionState, TaskStatus
from app.utils.prompting import normalize_camera_spec
from app.poller import poller
from app.scheduler import scheduler
from app.agent.chat_api import router as agent_chat_router
from app.controller.novel_api import router as novel_api_router
from app.controller.novel_anchors_api import router as novel_anchors_router
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
        # 轨迹完成事件 + 清理总线（节点可能已发过 completed，重复无害）
        await events.emit(state["session_id"], "completed", {})
        # 会话保留 1 小时用于查轨迹，之后释放，防止 _sessions 无限增长（内存泄漏）
        asyncio.get_running_loop().call_later(
            3600, _sessions.pop, state["session_id"], None)
        settled = True
    except Exception as exc:  # 节点异常 → 记 FAILED，不裸崩后台任务
        # 异常 str() 可能为空（如部分 asyncio 异常），兜底用异常类型名；
        # 用户侧文案友好化，完整异常只进日志
        msg = friendly_error_message(exc)
        logger.error(f"Session {state['session_id']} failed", exc_info=exc)
        state["status"] = TaskStatus.FAILED
        state["error_message"] = msg
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
        # 会话保留 1 小时用于查轨迹，之后释放，防止 _sessions 无限增长（内存泄漏）
        asyncio.get_running_loop().call_later(
            3600, _sessions.pop, state["session_id"], None)
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
            await session_store.delete_session(state["session_id"])


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
    await poller.start()
    await scheduler.start(runner=_run_session)
    # 启动自动恢复：把上次进程被杀时未完成的会话从 Redis 快照里捡回来断点续跑。
    # 用 create_task 异步执行——恢复流程要读 Redis、可能还要查 agnes，
    # 绝不能阻塞服务启动；内部已整体 try/except，失败只 log。
    from app.recovery import recover_active_sessions

    asyncio.create_task(recover_active_sessions())


@app.post("/v1/tasks/video", status_code=202, response_model=ApiResponse)
async def create_video_task(req: CreateVideoTaskRequest) -> ApiResponse:
    if not req.prompt.strip():
        raise AppError("prompt 不能为空", status_code=422)

    session_id = uuid.uuid4().hex[:12]

    if scheduler.snapshot()["queued_count"] >= _queue_maxsize():
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
    position = scheduler.submit(session_id)
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
async def task_events(session_id: str):
    """SSE 轨迹事件流：节点实时发射 node/tool/progress 事件。"""
    queue = await events.subscribe(session_id)

    async def generator():
        try:
            # 每 15s 发一次心跳注释行，防止代理超时断连
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield events.sse_format(event)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            # 消费者断开 → 清理总线
            await events.unsubscribe(session_id)

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.get("/v1/tasks/{session_id}", response_model=ApiResponse)
async def get_task(session_id: str) -> ApiResponse:
    state = _sessions.get(session_id)
    if not state:
        raise AppError("session 不存在", status_code=404)
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
        }
    )


def _queue_maxsize() -> int:
    from app.config import settings
    return settings.session_queue_maxsize


@app.on_event("shutdown")
async def _shutdown() -> None:
    from app.gateway.agnes import gateway as ag
    await ag.close()
    await poller.stop()
    await scheduler.stop()
    await session_store.close()