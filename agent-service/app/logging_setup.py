"""把根 logger 额外写一份到轮转文件（可观测性）。

**为什么必须有这个模块**

本项目 20 多个模块都用 `logging.getLogger(__name__)`，但**从未配置过任何 handler** ——
日志只到 stdout。后果是**一次手动重启就抹掉全部诊断证据**。

实测代价（2026-09-15，批次 B 上线当天）：一个真实任务 13:07 进入了 `fix_looping`、
13:12 变成 `failed`，但 `video_urls` 从 1 变成 0、`qc_report` 从「有」变「无」——
想定位只能靠猜。三件事凑齐导致无法取证：

1. stdout 日志随手动重启消失（没有文件）
2. `events.py` **无缓冲**（无订阅即丢弃），事后取不回 node 事件
3. `recovery` 在同一个 session_id 下重跑，覆盖了内存里的 state

只要三者中有一样，那次排查本可以是「grep 一下日志」的事。

**为什么不 import 时自动执行**：那样 pytest 导入 `app.main` 就会往仓库里写
`data/logs/`。改为由 `main.py` 的启动钩子显式调用，并额外用 `pytest` 检测兜底。
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# agent-service/ 根目录（本文件在 agent-service/app/ 下）
_SERVICE_ROOT = Path(__file__).resolve().parents[1]

_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 10 MB × 5 份：足够回溯数天的真实任务，又不会无限吃盘
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_configured_path: Path | None = None


def _in_test_env() -> bool:
    """是否跑在 pytest 里。抽成函数是为了让用例能替换它去测配置分支。"""
    return "pytest" in sys.modules


def resolve_log_dir(log_dir: str | os.PathLike[str] | None = None) -> Path:
    """把配置里的日志目录解析成绝对路径（相对路径按 agent-service/ 解析）。"""
    if log_dir is None:
        from app.config import settings

        log_dir = settings.log_dir
    p = Path(log_dir)
    if not p.is_absolute():
        p = _SERVICE_ROOT / p
    return p


def _already_has_handler(logger: logging.Logger, target: Path) -> bool:
    """幂等检查：避免 uvicorn --reload 或多进程下重复挂 handler（日志会翻倍）。"""
    target = target.resolve()
    for h in logger.handlers:
        base = getattr(h, "baseFilename", None)
        if base and Path(base).resolve() == target:
            return True
    return False


def setup_file_logging(
    log_dir: str | os.PathLike[str] | None = None,
    level: str | None = None,
    *,
    force: bool = False,
) -> Path | None:
    """给**根 logger** 挂一个轮转文件 handler。

    挂在根 logger 上是有意的：各模块的 `logging.getLogger(__name__)` 默认
    `propagate=True`，一次挂载即可覆盖全部模块，不必逐个改。

    Args:
        log_dir: 日志目录；None = 读 `settings.log_dir`
        level: 日志级别；None = 读 `settings.log_level`
        force: 绕过幂等与 pytest 检测（测试用）

    Returns:
        实际写入的日志文件路径；未启用或失败时返回 None（**绝不抛异常**：
        日志配置失败不该让服务起不来）。
    """
    global _configured_path

    if not force:
        # 测试环境不往仓库写日志（导入 app.main 的用例不少）
        if _in_test_env():
            return None
        try:
            from app.config import settings

            if not settings.log_to_file:
                return None
        except Exception:
            pass

    try:
        from app.config import settings

        if level is None:
            level = settings.log_level
    except Exception:
        if level is None:
            level = "INFO"

    target_dir = resolve_log_dir(log_dir)
    log_path = target_dir / "agent.log"

    root = logging.getLogger()
    # 幂等：**不受 force 影响**。force 只用来绕过 pytest / 配置门禁，
    # 不能用来绕过重复挂载保护 —— 否则 uvicorn --reload 或重复调用会让日志翻倍、
    # 轮转错乱（实测：force=True 连调两次挂了 2 个 handler）。
    if _already_has_handler(root, log_path):
        _configured_path = log_path
        return log_path

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8", delay=True,
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(handler)
        # 只在根级别更「不严」时才放宽，避免把别人的 WARNING 覆盖成 DEBUG
        lvl = getattr(logging, str(level).upper(), logging.INFO)
        if root.level == logging.NOTSET or root.level > lvl:
            root.setLevel(lvl)
        _configured_path = log_path
        return log_path
    except Exception as exc:  # 磁盘满/权限不足都不该让服务起不来
        logging.getLogger(__name__).warning("文件日志初始化失败（仅 stdout）: %s", exc)
        return None


def configured_log_path() -> Path | None:
    """已配置的日志文件路径（未配置返回 None）。"""
    return _configured_path
