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
    # 图片合成视频：从已有图片直接拼成片（ffmpeg 幻灯片，不消耗 agnes 额度）
    slideshow: NotRequired[bool]
    slideshow_images: NotRequired[list]
    slide_seconds: NotRequired[float]

    # === 可灵式精细控制 ===
    # 全局风格提示词（折进每镜提示词正文；agnes 无独立 style 字段）
    style_prompt: NotRequired[str]
    # 负面提示词（agnes 无 negative_prompt 字段，折成「避免出现：…」进正文）
    negative_prompt: NotRequired[str]
    # 时间轴：总时长（秒）。给了则覆盖 LLM 对 brief 的时长猜测
    total_seconds: NotRequired[int]
    # 时间轴：镜头数。给了则约束 LLM 分镜数量，每镜时长 = 总时长 / 镜头数
    shot_count: NotRequired[int]
    # 全局运镜倾向：{shot_size, angle, movement}（标准模式 LLM 自由分镜时注入）
    shot_language: NotRequired[dict]
    # 元素语义绑定：[{name, image_index}]，image_index 为 1-based（对应 <Picture N>）
    reference_bindings: NotRequired[list]

    # === 各节点产出（全部落 State → Checkpoint 序列化，断点恢复用）===
    brief: NotRequired[dict]
    script: NotRequired[list]
    storyboard: NotRequired[list]
    assets: NotRequired[list]
    video_urls: NotRequired[list]
    video_ids: NotRequired[list]
    # asset_fetch 落到本地的分段视频路径（list[str]，与 video_urls 同长同序；
    # 下载失败的索引为 "" 占位。QC 只能检本地文件，agnes 直链检不了）
    local_video_paths: NotRequired[list]
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