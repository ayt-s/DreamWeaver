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
    # 生成类型：text_video(纯文本视频)/image_video(图生视频)/text_image(文生图)
    gen_type: NotRequired[str]
    # 用户上传的参考图片 URL（图生视频模式；空则走文生图自动喂）
    reference_images: NotRequired[list]
    # 无限画布图生视频：用户自定片段列表 [{image_url, prompt, seconds}]，
    # 每段一镜生成几秒小视频，最后由 synthesizer 拼接成一条长视频
    segments: NotRequired[list]

    # === 各节点产出（全部落 State → Checkpoint 序列化，断点恢复用）===
    brief: NotRequired[dict]
    script: NotRequired[list]
    storyboard: NotRequired[list]
    assets: NotRequired[list]
    video_urls: NotRequired[list]
    video_ids: NotRequired[list]
    image_urls: NotRequired[list]
    # synthesizer 拼接后的长视频 URL（画布模式产物）
    final_video_url: NotRequired[str]
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