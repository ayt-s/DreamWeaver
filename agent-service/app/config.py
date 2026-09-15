"""DreamWeaver Agent 服务配置。

所有密钥走环境变量，绝不落代码。

"""
import os

from dotenv import load_dotenv

# 必须在 Settings() 实例化之前加载 .env，否则模块导入时读到的全是空值
load_dotenv()


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
    # 提交总尝试次数（含 429/503 退避重试）。多 provider 场景下撞墙就切账号，
    # 不依赖加长退避。每 provider 最坏 6 次 × 30s 封顶 ≈ 2-3 分钟，双 provider 总 ~6 分钟。
    #   429 退避封顶 30s + ±20% 抖动防并发会话同时退避完撞墙
    #   503 队列满封顶 60s + 抖动（队列消化需时间）
    video_submit_max_attempts: int = int(_env("AGNES_VIDEO_MAX_ATTEMPTS", "6"))

    # Phase 2 回调目标（Java Spring Boot 地址）
    java_notify_url: str = _env("JAVA_NOTIFY_URL", "")

    # === 会话持久化（Redis 快照 + 启动自动恢复 + 心跳续期）===
    # agent 用 db=1，避免与 Java Redisson 的 dw:task:watchdog 混在 db0
    redis_url: str = _env("AGENT_REDIS_URL", "redis://127.0.0.1:6379/1")
    # state 快照 / progress TTL（秒），默认 24h
    session_snapshot_ttl_s: int = int(_env("AGENT_SESSION_SNAPSHOT_TTL_S", "86400"))
    # 心跳间隔（秒）：每 interval 秒 POST 一次 {java_notify_url}/internal/heartbeat，
    # 让 Java 侧重武装看门狗 TTL（把「固定截止时间」变成「空闲超时」）
    heartbeat_interval_s: int = int(_env("AGENT_HEARTBEAT_INTERVAL_S", "60"))

    # === fix_looping 镜级自愈（B1）===
    # 修正后缀策略（决定失败镜重生时是否追加按原因映射的修正指令）：
    #   off        不带后缀，原样重生
    #   mechanism  总是带按原因映射的后缀（如模糊→steady shot/slow camera）
    #   random50   50/50 随机 —— B0 阶段 3 的在线 A/B：零额外成本地从真实流量
    #              得到「后缀有没有用」的答案，fix_history[*].used_hint 记录分组
    fix_hint_mode: str = _env("AGENT_FIX_HINT_MODE", "random50")

    # === 可观测性：文件日志 ===
    # **为什么必须是文件**：本项目所有模块都用 logging.getLogger(__name__)，但从未配置
    # 过任何 handler —— 日志只到 stdout。于是**一次手动重启就抹掉全部诊断证据**。
    # 实测代价（2026-09-15）：一个真实任务 13:07 进入 fix_looping、13:12 变 failed，
    # 但 video_urls 从 1 变成 0、qc_report 从有变无 —— 想定位只能猜。
    # 三样凑齐导致无法取证：stdout 日志随重启消失 + events.py 无缓冲
    # + recovery 在同 id 下重跑覆盖内存 state。
    log_to_file: bool = _env("AGENT_LOG_TO_FILE", "1").strip().lower() not in (
        "0", "false", "no", "off")
    # 相对路径按 <agent-service>/ 解析；空串 = data/logs
    log_dir: str = _env("AGENT_LOG_DIR", "").strip() or "data/logs"
    log_level: str = _env("AGENT_LOG_LEVEL", "INFO").strip().upper() or "INFO"

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.agnes_api_key}",
            "Content-Type": "application/json",
        }


settings = Settings()