"""DreamWeaver Agent 服务配置。

所有密钥走环境变量，绝不落代码。

"""
import os


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


class Settings:
    """Phase 1 最小配置集。"""

    # Agnes API（只存在于本层，前端与 Java 都拿不到）
    agnes_base_url: str = _env("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
    agnes_api_key: str = _env("AGNES_API_KEY")

    # 模型
    text_model: str = _env("AGNES_TEXT_MODEL", "agnes-2.5-flash")
    image_model: str = _env("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash")
    video_model_fast: str = _env("AGNES_VIDEO_FAST", "agnes-video-2.5-flash")
    video_model_hd: str = _env("AGNES_VIDEO_HD", "agnes-video-2.5")

    # 生成默认值
    default_seconds: str = "5"          # 视频时长字符串 "4"~"12"
    default_aspect_ratio: str = "16:9"  # 画幅白名单见设计文档

    # 轮询（Phase 1 内联轮询用，Phase 2 移交独立 Poller）
    poll_interval_s: int = 5
    video_timeout_s: int = 900          # 单任务轮询上限 15 分钟

    # 任务调度队列（排期）：控制同时执行的会话上限，避免无界并发打到 Agnes 限流
    max_concurrent_sessions: int = int(_env("AGENT_MAX_CONCURRENT_SESSIONS", "2"))
    session_queue_maxsize: int = int(_env("AGENT_SESSION_QUEUE_SIZE", "200"))

    # 视频接口限流适配（实测：平台视频 RPM≈2/分钟，队列满 503 常见）
    # 提交节流：两次 /videos 提交最小间隔（秒）→ 默认 35s ≈ 1.7 次/分钟，给余量
    video_submit_interval_s: float = float(_env("AGNES_VIDEO_SUBMIT_INTERVAL_S", "35"))
    # 提交总尝试次数（含 429/503 退避重试），视频队列繁忙时最长约 5 分钟内仍失败才放弃
    video_submit_max_attempts: int = int(_env("AGNES_VIDEO_MAX_ATTEMPTS", "6"))

    # Phase 2 回调目标（Java Spring Boot 地址）
    java_notify_url: str = _env("JAVA_NOTIFY_URL", "")

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.agnes_api_key}",
            "Content-Type": "application/json",
        }


settings = Settings()