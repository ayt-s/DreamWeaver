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

logger = logging.getLogger(__name__)

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import events
from app.errors import AppError, friendly_error_message, register_exception_handlers
from app.graph import compiled_graph
from app.state import CreativeSessionState, TaskStatus
from app.poller import poller
from app.scheduler import scheduler
from app.agent.chat_api import router as agent_chat_router
from app.controller.novel_api import router as novel_api_router

app = FastAPI(title="DreamWeaver Agent Service", version="0.2.0")
register_exception_handlers(app)

# 本地产物静态目录（与 nodes/synthesizer.py OUTPUT_ROOT 对应）：
# /v1/files/<session>/final.mp4 → web-frontend vite 代理 /v1 → 8000，可直接 <video> 播放
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/v1/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")

# 聊天 Agent 路由（Phase 1）：POST /v1/agent/chat
app.include_router(agent_chat_router)

# 小说转漫剧预处理路由：POST /v1/novel/preprocess
app.include_router(novel_api_router)

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


def _parse_segments(raw: str | None) -> list:
    """解析无限画布片段 JSON：[] -> [{image_url, prompt, seconds}]。

    只保留带 image_url 的条目，其余字段尽量宽容。
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        segments = []
        for s in parsed:
            if not isinstance(s, dict) or not s.get("image_url"):
                continue
            try:
                seconds = int(s.get("seconds", 5) or 5)
            except (TypeError, ValueError):
                seconds = 5
            segments.append({
                "image_url": str(s.get("image_url", "")).strip(),
                "prompt": str(s.get("prompt", "")).strip(),
                "seconds": seconds,
                "aspect_ratio": str(s.get("aspect_ratio") or "16:9").strip(),
            })
        return segments
    except json.JSONDecodeError:
        logger.warning("segments 不是合法 JSON 数组: %s", raw[:100])
        return []


async def _run_session(state: CreativeSessionState) -> None:
    """后台执行 LangGraph。由 SessionScheduler 调用（有界并发）。"""
    config = {"configurable": {"thread_id": state["session_id"]}}
    try:
        result = await compiled_graph.ainvoke(state, config=config)
        _sessions[state["session_id"]] = result
        # 轨迹完成事件 + 清理总线（节点可能已发过 completed，重复无害）
        await events.emit(state["session_id"], "completed", {})
        # 会话保留 1 小时用于查轨迹，之后释放，防止 _sessions 无限增长（内存泄漏）
        asyncio.get_running_loop().call_later(
            3600, _sessions.pop, state["session_id"], None)
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


@app.on_event("startup")
async def _startup() -> None:
    await poller.start()
    await scheduler.start(runner=_run_session)


@app.post("/v1/tasks/video", status_code=202, response_model=ApiResponse)
async def create_video_task(req: CreateVideoTaskRequest) -> ApiResponse:
    if not req.prompt.strip():
        raise AppError("prompt 不能为空", status_code=422)

    session_id = uuid.uuid4().hex[:12]

    if scheduler.snapshot()["queued_count"] >= _queue_maxsize():
        raise AppError("任务队列已满，请稍后再试", status_code=429, retryable=True)

    ref_images = _parse_json_list(req.reference_images, "reference_images")
    segments = _parse_segments(req.segments)
    state: CreativeSessionState = {
        "session_id": session_id,
        "user_id": req.user_id or "demo-user",
        "raw_prompt": req.prompt,
        "gen_type": req.gen_type or "text_video",
        "reference_images": ref_images,
        "segments": segments,
        "status": TaskStatus.QUEUED,
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
        "trace": [],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    _sessions[session_id] = state
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