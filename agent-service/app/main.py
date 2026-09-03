# DreamWeaver — Agent 服务入口
"""FastAPI 入口（Phase 1 MVP）。

- POST /v1/tasks/video  提交创作任务（后台跑 LangGraph，返回 session_id）
- GET  /v1/tasks/{id}   查询任务状态（含中间产物，前端轨迹展示用）
- GET  /v1/tasks/{id}/events  SSE 轨迹事件（Phase 1 简化：轮询状态接口兜底）

Phase 1 单机运行可接受；排队/限流/鉴权在 Phase 2 接入。
"""
import asyncio
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

app = FastAPI(title="DreamWeaver Agent Service", version="0.1.0")

# 内存态会话仓（Phase 1）。生产换 PostgreSQL 落库（设计文档 §4.1）
_sessions: dict[str, CreativeSessionState] = {}
_tasks: dict[str, asyncio.Task] = {}


# 统一响应体（与 Java CommonResult 对齐：code/message/data）
class ApiResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Optional[dict] = None


class CreateVideoTaskRequest(BaseModel):
    prompt: str
    user_id: Optional[str] = "demo-user"


class CreateVideoTaskResponse(BaseModel):
    session_id: str
    status: str


async def _run_session(state: CreativeSessionState) -> None:
    """后台执行 LangGraph。Phase 2 改为队列 + 断点恢复接入。"""
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


@app.post("/v1/tasks/video", status_code=202, response_model=ApiResponse)
async def create_video_task(req: CreateVideoTaskRequest) -> ApiResponse:
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt 不能为空")

    session_id = uuid.uuid4().hex[:12]
    state: CreativeSessionState = {
        "session_id": session_id,
        "user_id": req.user_id or "demo-user",
        "raw_prompt": req.prompt,
        "status": TaskStatus.PENDING,
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
        "trace": [],
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    _sessions[session_id] = state
    _tasks[session_id] = asyncio.create_task(_run_session(state))
    return ApiResponse(
        code=0,
        message="ok",
        data={"session_id": session_id, "status": TaskStatus.PENDING}
    )


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
            "error_message": state.get("error_message"),
        }
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    from app.gateway.agnes import gateway as ag
    await ag.close()
    await poller.stop()
