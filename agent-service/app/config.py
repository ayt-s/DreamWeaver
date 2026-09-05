"""DreamWeaver Agent 服务配置。

所有密钥走环境变量，绝不落代码。

"""
import os


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _load_agnes_providers() -> list[dict]:
    """加载 Agnes 多端点池（同家供应商多账号，官方允许的扩容）。

    - 默认: 国际 AGNES_BASE_URL + AGNES_API_KEY
    - 可选: 国内 AGNES_BASE_URL_cn + AGNES_API_KEY_cn
    未配置的端点自动从池中排除；只有 1 个端点时等价于单点。
    """
    providers: list[dict] = []
    intl_base = _env("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
    intl_key = _env("AGNES_API_KEY")
    if intl_key:
        providers.append({"name": "intl", "base_url": intl_base, "api_key": intl_key})
    cn_base = _env("AGNES_BASE_URL_cn")
    cn_key = _env("AGNES_API_KEY_cn")
    if cn_base and cn_key:
        providers.append({"name": "cn", "base_url": cn_base, "api_key": cn_key})
    return providers


class Settings:
    """Phase 1 最小配置集。"""

    # Agnes API - 多端点池（同家供应商双账号扩容）
    # intl 用默认 AGNES_BASE_URL/AGNES_API_KEY；cn 用 AGNES_BASE_URL_cn/AGNES_API_KEY_cn
    # 未配置的端点自动排除；gateway 按池大小做 round-robin + failover
    agnes_providers: list[dict] = _load_agnes_providers()

    # Agnes API（单点默认：国际端点，chat/novel 走这里）
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
    # 提交总尝试次数（含 429/503 退避重试）。加大退避后最坏 ~7.5 分钟：
    #   429 退避封顶 60s（10 次全 429 ≈ 5-6 分钟）；503 队列满封顶 120s（10 次 ≈ 7 分钟）
    #   加 ±20% 抖动防止并发会话同时退避完撞墙
    video_submit_max_attempts: int = int(_env("AGNES_VIDEO_MAX_ATTEMPTS", "10"))

    # Phase 2 回调目标（Java Spring Boot 地址）
    java_notify_url: str = _env("JAVA_NOTIFY_URL", "")

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.agnes_api_key}",
            "Content-Type": "application/json",
        }


settings = Settings()