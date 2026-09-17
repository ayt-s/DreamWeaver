"""批次 H：LangSmith 架包的开关语义。

## 为什么这组测试值得存在

「默认关闭、关闭时零开销」是一个**很容易说出口但很难保持**的承诺：
只要有人在出口函数里顺手 `from langsmith import traceable`，它就在每次请求上
先生成一个 trace 上下文（客户端 + 后台线程 + 序列化开销），关闭也照样花。

所以这里锁三条：
1. **关闭时绝不触碰 langsmith**（把 `traceable` 换成「一调用就炸」的哨兵来证明）
2. **没配 key 也算关闭** —— 否则每次调用都会尝试上报并刷错误日志
3. **包装/上报失败降级为直通** —— 可观测性挂掉绝不能拖垮生成主流程

顺带锁住接线本身：`gateway/agnes.py` 的四个出站方法必须都带着装饰器
（有人删掉 `@traced` 时这里会红，而不是等三个月后发现「trace 里怎么没有 LLM 调用」）。

⚠️ 一个容易写错的文档事实：`langsmith` **模块本身**早已被 langgraph → langchain-core
间接导入，所以「关闭时不 import langsmith」是不成立的。省下的是**包装与客户端初始化**。
"""

import logging
import sys

import pytest

from app.config import settings
from app.utils import observability


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每个用例都从「未缓存 + 无 key」的干净状态开始。"""
    observability.reset_cache()
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.setattr(settings, "langsmith_tracing", False, raising=False)
    yield
    observability.reset_cache()


def _spy_traceable(calls: list, *, raises: Exception | None = None):
    """替掉 langsmith.traceable，记录调用参数（可选：调用即抛）。"""
    def fake_traceable(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        if raises is not None:
            raise raises

        def deco(fn):
            return fn
        return deco

    return fake_traceable


# ------------------------------------------------------------------ 开关判定


def test_disabled_by_default():
    assert observability.enabled() is False


def test_key_without_switch_is_disabled(monkeypatch):
    """配了 key 但开关没开 → 仍不上报（默认必须保守）。"""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")
    assert observability.enabled() is False


def test_switch_without_key_is_disabled(monkeypatch):
    """★ 开了开关但没 key → 视为关闭。

    否则每次 chat/图像/视频调用都会尝试上报并报错 —— 纯噪声，还会拖慢主流程。
    """
    monkeypatch.setattr(settings, "langsmith_tracing", True, raising=False)
    assert observability.enabled() is False


def test_enabled_requires_switch_and_key(monkeypatch):
    monkeypatch.setattr(settings, "langsmith_tracing", True, raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")
    assert observability.enabled() is True


def test_legacy_key_env_is_accepted(monkeypatch):
    """兼容旧变量名 LANGCHAIN_API_KEY（langsmith 自己也认这个）。"""
    monkeypatch.setattr(settings, "langsmith_tracing", True, raising=False)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_legacy")
    assert observability.enabled() is True


# ------------------------------------------------------- 关闭时零触碰（核心）


@pytest.mark.asyncio
async def test_off_path_never_touches_langsmith(monkeypatch):
    """★ 关闭时 `traceable` 一次都不能被调用。

    把 `langsmith.traceable` 换成「一调用就抛」的哨兵：如果实现里顺手碰了
    langsmith，这个用例会以异常失败，而不是静默地每次都建 trace 上下文。
    """
    import langsmith

    def boom(*args, **kwargs):
        raise AssertionError("关闭状态下不该触碰 langsmith.traceable")

    monkeypatch.setattr(langsmith, "traceable", boom)

    @observability.traced("unit.off", run_type="llm")
    async def fn(x: int) -> int:
        return x * 2

    assert await fn(21) == 42, "关闭时必须直通并保持返回值"


@pytest.mark.asyncio
async def test_off_path_keeps_function_identity():
    """关装饰器不能吃掉函数名/文档 —— 日志与栈信息都靠它。"""
    @observability.traced("unit.identity")
    async def my_outbound_call() -> str:
        """docstring 要留着"""
        return "ok"

    assert my_outbound_call.__name__ == "my_outbound_call"
    assert my_outbound_call.__doc__ == "docstring 要留着"
    assert await my_outbound_call() == "ok"


# ---------------------------------------------------------------- 开启时行为


@pytest.mark.asyncio
async def test_on_path_wraps_once_then_reuses(monkeypatch):
    """开启时：包装一次并缓存，不是每次调用都重新装饰。"""
    import langsmith

    monkeypatch.setattr(settings, "langsmith_tracing", True, raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")
    calls: list = []
    monkeypatch.setattr(langsmith, "traceable", _spy_traceable(calls))

    @observability.traced("unit.on", run_type="tool")
    async def fn(x: int) -> int:
        return x + 1

    assert await fn(1) == 2
    assert await fn(2) == 3
    assert len(calls) == 1, "第二次调用应命中缓存"
    assert calls[0]["kwargs"] == {"name": "unit.on", "run_type": "tool"}


@pytest.mark.asyncio
async def test_wrap_failure_degrades_to_direct_call(monkeypatch, caplog):
    """★ 包装失败（SDK 版本不符 / 端点畸形 / 客户端初始化炸）→ 直通，不打断主流程。"""
    import langsmith

    monkeypatch.setattr(settings, "langsmith_tracing", True, raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")
    monkeypatch.setattr(langsmith, "traceable",
                        _spy_traceable([], raises=RuntimeError("SDK 炸了")))

    @observability.traced("unit.broken")
    async def fn() -> str:
        return "仍然要成功"

    with caplog.at_level(logging.WARNING):
        assert await fn() == "仍然要成功"

    assert any("降级" in r.message for r in caplog.records), "降级应留下告警"


@pytest.mark.asyncio
async def test_exception_from_wrapped_call_propagates(monkeypatch):
    """业务异常必须原样抛出 —— 不能被 observability 吞掉。"""
    import langsmith

    monkeypatch.setattr(settings, "langsmith_tracing", True, raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_fake")
    monkeypatch.setattr(langsmith, "traceable", _spy_traceable([]))

    @observability.traced("unit.raises")
    async def fn() -> None:
        raise ValueError("业务失败")

    with pytest.raises(ValueError, match="业务失败"):
        await fn()


# -------------------------------------------------------------------- 接线


@pytest.mark.real_gateway_method  # 需要看真实方法本身（conftest 的哨兵会覆盖掉 __wrapped__）
def test_gateway_outbound_methods_are_wrapped():
    """★ 四个出站方法都必须带装饰器。

    锁定接线：有人删掉 `@traced` 时这里会红，而不是等到某天发现
    「LangSmith 里怎么没有 LLM 调用」再回头查。
    `functools.wraps` 会留下 `__wrapped__`，用它判断。
    """
    from app.gateway.agnes import AgnesGateway

    for name in ("chat", "generate_image", "submit_video", "query_video"):
        method = getattr(AgnesGateway, name)
        assert hasattr(method, "__wrapped__"), f"AgnesGateway.{name} 没有 @traced 装饰"


def test_gateway_import_does_not_break_with_langsmith_missing(monkeypatch):
    """langsmith 缺失/装不上时，gateway 仍能导入与被调用（可观测性不是硬依赖）。"""
    import importlib

    import app.gateway.agnes as agnes_mod

    monkeypatch.setitem(sys.modules, "langsmith", None)  # 模拟「装了但坏了」

    reloaded = importlib.reload(agnes_mod)
    assert reloaded.AgnesGateway is not None
