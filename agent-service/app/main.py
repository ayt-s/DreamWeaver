# DreamWeaver — Agent 服务入口
"""FastAPI 入口。

- POST /v1/tasks/video  提交创作任务（进入调度队列，返回 session_id + 排队编号）
- POST /v1/tasks/{id}/cancel  取消排队中的会话（已开始执行则 409）
- GET  /v1/tasks/{id}   查询任务状态（含中间产物，前端轨迹展示用）
- GET  /v1/tasks/{id}/events  SSE 轨迹事件（Phase 1 简化：轮询状态接口兜底）
- GET  /v1/scheduler    调度队列快照（执行中 / 排队中，画廊排期展示用）

并发控制：SessionScheduler 有界并发（默认同时执行 2 个会话），
超出的会话进入 FIFO 队列排队，避免多个长任务同时压向 Agnes API 触发限流。
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import events
from app.graph import compiled_graph
from app.state import CreativeSessionState, TaskStatus
from app.poller import poller
from app.scheduler import scheduler

app = FastAPI(title="DreamWeaver Agent Service", version="0.2.0")

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
    # 生成类型：text_video(纯文本视频)/image_video(图生视频)/novel_image(小说转图)
    gen_type: Optional[str] = "text_video"
    # 用户上传的参考图片 URL 数组（JSON 字符串，Java 侧原样透传）；空则文生图自动喂
    reference_images: Optional[str] = None


class CreateVideoTaskResponse(BaseModel):
    session_id: str
    status: str


async def _run_session(state: CreativeSessionState) -> None:
    """后台执行 LangGraph。由 SessionScheduler 调用（有界并发）。"""
    config = {"configurable": {"thread_id": state["session_id"]}}
    try:
        result = await compiled_graph.ainvoke(state, config=config)
        _sessions[state["session_id"]] = result
        # 轨迹完成事件 + 清理总线
        await events.emit(state["session_id"], "completed", {})
    except Exception as exc:  # 节点异常 → 记 FAILED，不裸崩后台任务
        logger.error(f"Session {state['session_id']} failed: {exc}")
        state["status"] = TaskStatus.FAILED
        state["error_message"] = str(exc)
        _sessions[state["session_id"]] = state
        await events.emit(state["session_id"], "failed", {"error": str(exc)})
        # Phase 2 回调通知失败状态
        from app.callback.java_notify import notify_java_completion
        asyncio.create_task(
            notify_java_completion(
                session_id=state["session_id"],
                status=TaskStatus.FAILED,
                error_message=str(exc),
            )
        )
        # 不 re-raise：避免 "Task exception was never retrieved" 日志污染


@app.on_event("startup")
async def _startup() -> None:
    await poller.start()
    await scheduler.start(runner=_run_session)


@app.post("/v1/tasks/video", status_code=202, response_model=ApiResponse)
async def create_video_task(req: CreateVideoTaskRequest) -> ApiResponse:
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt 不能为空")

    session_id = uuid.uuid4().hex[:12]

    if scheduler.snapshot()["queued_count"] >= _queue_maxsize():
        raise HTTPException(status_code=429, detail="任务队列已满，请稍后再试")

    # Java 侧透传的 reference_images 是 JSON 数组字符串，解析失败则视为未传
    ref_images: list = []
    if req.reference_images:
        try:
            parsed = json.loads(req.reference_images)
            if isinstance(parsed, list):
                ref_images = parsed
        except json.JSONDecodeError:
            logger.warning(
                "reference_images 不是合法 JSON 数组: %s",
                req.reference_images[:100],
            )
    state: CreativeSessionState = {
        "session_id": session_id,
        "user_id": req.user_id or "demo-user",
        "raw_prompt": req.prompt,
        "gen_type": req.gen_type or "text_video",
        "reference_images": ref_images,
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
        raise HTTPException(status_code=404, detail="session 不存在")
    if scheduler.cancel(session_id):
        _sessions[session_id]["status"] = TaskStatus.FAILED
        _sessions[session_id]["error_message"] = "用户取消排队"
        return ApiResponse(
            code=0,
            message="ok",
            data={"session_id": session_id, "canceled": True},
        )
    raise HTTPException(status_code=409, detail="会话已开始执行，无法取消")


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
        raise HTTPException(status_code=404, detail="session 不存在")
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