"""LangGraph State 定义（对应设计文档 §1.1）。"""
from typing import TypedDict, NotRequired
from enum import Enum

# 转发导出：trace 的键白名单与写入助手都住在 utils.trace（那里有详细口径说明）。
# 放在这里是为了让「state 里 trace 是什么形状」这件事在字段旁边就能看到。
from app.utils.trace import TRACE_KEYS, TRACE_MAX  # noqa: F401


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
    # 直出图（画布节点「一键文生图」）：跳过需求解析/剧本/分镜，直接按 raw_prompt 出图
    direct_image: NotRequired[bool]
    # 直出图的候选张数（1~5）：同 prompt 多次请求，产出多个候选供人选一张
    image_count: NotRequired[int]
    # 出图画幅（如 "16:9"）：**必须显式传**，不传服务端按 1:1 出正方形
    # （2026-09-18 实测：项目真实产物 18/18 都是 1024x1024，而视频是 16:9）。
    # 画布节点来自 data.ratio；标准模式兜底用分镜的 aspect_ratio。
    image_ratio: NotRequired[str]
    # 首帧锁定（keyframe）：有首帧图时把它作为视频的**实际第一帧**，
    # 而不是塞进参考图数组（reference 模式官方明确「可能重新构图/重新计时」）
    lock_first_frame: NotRequired[bool]
    # 段间衔接：把下一段的首帧当本段尾帧（last_frame），让相邻段首尾接得上
    chain_frames: NotRequired[bool]
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
    # fix_looping 检测到会话已中止（用户删任务 / 全量重生后心跳回 tracked=false）
    # → 立即放弃修复，不再自动重生（否则每轮都是白烧的 agnes 调用）
    fix_aborted: NotRequired[bool]
    # 修复轮次用尽（节点在 fix_round + 1 > max_fix_rounds 时置位且不改动 storyboard）。
    # ⚠️ 必须有这个显式标记：若只靠路由比较 fix_round，节点空转 + 路由仍 retry
    #    会形成**无限循环**（重生没发生 → QC 不变 → 再回来）——
    #    实测表现为 GraphRecursionError: Recursion limit of 10007。
    fix_exhausted: NotRequired[bool]
    # 放弃修复（fix_give_up 节点置位）+ 人类可读原因，由 notify_final 上报给 Java
    fix_give_up: NotRequired[bool]
    fix_give_up_reason: NotRequired[str]
    # 视频生成阶段的逐段错误摘要（video_generator 写入，notify_final 兜底使用）
    video_error: NotRequired[str]
    # notify_final 已完成终态通知（防重复上报的标记 + 便于测试断言）
    final_notified: NotRequired[bool]

    # === 审计 ===
    # 极简轨迹：`[{node, status, elapsed_ms}]`（键白名单 = TRACE_KEYS，上限 TRACE_MAX）。
    # ⚠️ 只允许经 `app.utils.trace.append()` 写入 —— 手写会绕过白名单，
    #    历史上就是因为手写才把 `prompt_en` 正文写进了快照（P0-4 / 批次 C1）。
    #    LLM 调用级细节（提示词、模型、重试）走 LangSmith，不进 state。
    trace: NotRequired[list]
    error_message: NotRequired[str]

    # === 元数据 ===
    model_config: NotRequired[dict]
    created_at: NotRequired[int]
    updated_at: NotRequired[int]