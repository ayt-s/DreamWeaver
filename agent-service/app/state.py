"""LangGraph State 定义（对应设计文档 §1.1）。"""
from typing import TypedDict, NotRequired
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SCRIPT_WRITING = "script_writing"
    STORYBOARD_WRITING = "storyboard_writing"
    ASSET_GENERATING = "asset_generating"
    VIDEO_GENERATING = "video_generating"
    QC_CHECKING = "qc_checking"
    FIX_LOOPING = "fix_looping"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class GenerationTrace(TypedDict):
    """每次工具调用的完整审计记录。"""
    tool_name: str
    params: dict
    result: dict
    latency_ms: int
    timestamp: int
    retry_count: int


class CreativeSessionState(TypedDict):
    # === 输入 ===
    session_id: str
    user_id: str
    raw_prompt: str

    # === 各节点产出（全部落 State → Checkpoint 序列化，断点恢复用）===
    brief: NotRequired[dict]
    script: NotRequired[list]
    storyboard: NotRequired[list]
    assets: NotRequired[list]
    video_urls: NotRequired[list]
    video_ids: NotRequired[list]
    image_urls: NotRequired[list]
    qc_report: NotRequired[dict]

    # === 控制流 ===
    status: NotRequired[TaskStatus]
    fix_round: NotRequired[int]
    max_fix_rounds: NotRequired[int]
    fix_history: NotRequired[list]

    # === 审计 ===
    trace: NotRequired[list]
    error_message: NotRequired[str]

    # === 元数据 ===
    model_config: NotRequired[dict]
    created_at: NotRequired[int]
    updated_at: NotRequired[int]