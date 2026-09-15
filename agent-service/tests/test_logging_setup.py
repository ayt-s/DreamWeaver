"""文件日志（可观测性）：`app/logging_setup.py`。

这一层的价值是「事后能查」。所以用例锁定的是**容易被无声破坏的几条约束**，
而不是覆盖率：

1. 日志真的落到文件里（模块 logger 通过根 logger 冒泡过去）
2. 幂等：重复调用不会挂两个 handler（否则日志翻倍、轮转错乱）
3. 测试环境**不往仓库写**日志（导入 app.main 的用例很多）
4. 目录不可写 / 配置异常时**不抛异常**（日志失败不该让服务起不来）
"""
import logging
from logging.handlers import RotatingFileHandler

import pytest

from app import logging_setup as ls


@pytest.fixture(autouse=True)
def _clean_root_handlers():
    """用例自己挂/卸 handler，跑完恢复根 logger 原状，避免污染其它测试。"""
    root = logging.getLogger()
    before = list(root.handlers)
    before_level = root.level
    yield
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)
            h.close()
    root.setLevel(before_level)


def _file_handlers(logger):
    return [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]


def test_log_line_actually_lands_in_file(tmp_path):
    """核心契约：模块 logger 写的东西必须能在文件里读到。"""
    path = ls.setup_file_logging(tmp_path, "INFO", force=True)
    assert path == tmp_path / "agent.log"

    logging.getLogger("app.some.module").info("批次B自愈循环进入 fix_looping")
    for h in _file_handlers(logging.getLogger()):
        h.flush()

    text = path.read_text(encoding="utf-8")
    assert "批次B自愈循环进入 fix_looping" in text
    assert "app.some.module" in text, "格式里应带 logger 名（否则定位不到模块）"


def test_creates_missing_directory(tmp_path):
    """目录不存在要自动创建（首次部署 / data 被清过）。"""
    nested = tmp_path / "a" / "b" / "logs"
    assert not nested.exists()

    path = ls.setup_file_logging(nested, "INFO", force=True)
    assert path is not None and nested.is_dir()


def test_idempotent_does_not_duplicate_handlers(tmp_path):
    """重复调用（uvicorn --reload / 多进程）不能挂第二个 handler。"""
    first = ls.setup_file_logging(tmp_path, "INFO", force=True)
    logging.getLogger("app.dup").info("只应出现一次")
    for h in _file_handlers(logging.getLogger()):
        h.flush()

    # 第二次配置同一个路径
    ls.setup_file_logging(tmp_path, "INFO", force=True)
    logging.getLogger("app.dup").info("又一条")
    for h in _file_handlers(logging.getLogger()):
        h.flush()

    handlers = [h for h in _file_handlers(logging.getLogger())
                if str(h.baseFilename).lower().endswith("agent.log")]
    assert len(handlers) <= 1, f"挂了 {len(handlers)} 个文件 handler"

    # 若真重复挂了，日志会翻倍 —— 用计数兜住
    text = first.read_text(encoding="utf-8")
    assert text.count("只应出现一次") == 1


def test_pytest_environment_does_not_write_repo_logs(tmp_path):
    """非 force 调用在 pytest 下必须直接放弃（否则跑测试就往仓库塞 data/logs/）。"""
    assert "pytest" in __import__("sys").modules, "前提：本用例本身在 pytest 下跑"
    assert ls.setup_file_logging(tmp_path, "INFO") is None
    assert not (tmp_path / "agent.log").exists()


def test_relative_log_dir_resolves_under_agent_service():
    """相对路径按 agent-service/ 解析 —— 否则从别的 cwd 启动会写错地方。"""
    resolved = ls.resolve_log_dir("data/logs")
    assert resolved.is_absolute()
    assert resolved.parts[-2:] == ("data", "logs")
    assert resolved.parent.parent.name == "agent-service", \
        f"应落在 agent-service/ 下，实际 {resolved}"


def test_disabled_by_config(monkeypatch, tmp_path):
    """settings.log_to_file=false 时完全不动作（一键闭嘴）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "log_to_file", False, raising=False)
    # 绕过 pytest 分支（它排在配置检查之前），否则测不到配置判断本身
    monkeypatch.setattr(ls, "_in_test_env", lambda: False)

    assert ls.setup_file_logging(tmp_path, "INFO") is None
    assert not (tmp_path / "agent.log").exists()


def test_config_can_enable_file_logging_outside_pytest(monkeypatch, tmp_path):
    """反向：非 pytest 且 log_to_file=true 时应当真的启用（否则线上永远没日志）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "log_to_file", True, raising=False)
    monkeypatch.setattr(settings, "log_level", "INFO", raising=False)
    monkeypatch.setattr(ls, "_in_test_env", lambda: False)

    path = ls.setup_file_logging(tmp_path, "INFO")
    assert path == tmp_path / "agent.log"
    assert path.parent.is_dir()


def test_unwritable_directory_does_not_raise(tmp_path):
    """日志初始化失败绝不抛异常 —— 不能让服务起不来。"""
    # 用一个「父路径是个文件」的非法目录
    bogus = tmp_path / "not_a_dir"
    bogus.write_text("i am a file", encoding="utf-8")

    result = ls.setup_file_logging(bogus, "INFO", force=True)
    assert result is None, "应当优雅降级为 None（仅 stdout）"


def test_configured_log_path_recorded(tmp_path):
    path = ls.setup_file_logging(tmp_path, "INFO", force=True)
    assert ls.configured_log_path() == path
