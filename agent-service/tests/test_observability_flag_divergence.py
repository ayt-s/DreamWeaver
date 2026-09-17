"""★ 我们的开关 与 langsmith SDK 的开关不得分歧（2026-09-17 实测踩到，代价一整轮排查）。

**现象**：`.env` 里写 `LANGSMITH_TRACING=1`。我们自己的 `enabled()`
（config 解析 `1/true/yes/on`）说「开」，但 **SDK 只认 `true` 这类字面量**，
它判定 `tracing_is_enabled() = False` → `traceable` **静默直通**：

    LangSmith tracing is not enabled, returning original function.

结果是 LangSmith 网页上一条 run 都没有、日志里也不报错 —— 看起来接好了，
其实什么都没上报。我当时先怀疑网络/代理/端点（381 错误方向），最后才发现
是 SDK 自己就没开。

**修法**：`_try_wrap` 在调用处 `with tracing_context(enabled=True)` 强制打开。
**这条测试在修复前是红的**（旧实现下 `seen["sdk_tracing"]` 是 False）。
"""

import pytest

from app.utils import observability


@pytest.mark.asyncio
async def test_our_flag_on_forces_sdk_tracing(monkeypatch):
    monkeypatch.setattr(observability.settings, "langsmith_tracing", True)
    monkeypatch.setenv("LANGSMITH_TRACING", "1")  # ← SDK 不认的写法，正是踩到的那个
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake_for_test")
    # 别真往 LangSmith 打：丢给一个必然拒绝的本地端口（断言不受上传结果影响）
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    observability.reset_cache()

    from langsmith.utils import tracing_is_enabled

    seen = {}

    async def inner():
        seen["sdk_tracing"] = tracing_is_enabled()
        return "ok"

    assert await observability.traced("flag_probe")(inner)() == "ok"
    assert seen["sdk_tracing"] is True, (
        "我们的开关为真时 SDK 必须真的在追踪 —— 否则就是「静默直通」，"
        "网页上什么都不会有（这正是 2026-09-17 的故障形态）"
    )


@pytest.mark.asyncio
async def test_flag_off_keeps_passthrough(monkeypatch):
    """反向护栏：我们的开关为假时**不包装、不建 run**（「默认关闭、直通」的承诺）。

    ⚠️ 不要断言 `tracing_is_enabled() is False` —— 那是 SDK 自己的全局标志，
    只要 `.env` 里 `LANGSMITH_TRACING=true` 它就是真，与我们包装不包装无关
    （我们关的时候根本不调 `traceable`，也就不会有 run 被创建/上报）。
    """
    monkeypatch.setattr(observability.settings, "langsmith_tracing", False)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake_for_test")
    observability.reset_cache()

    called = {}

    async def inner():
        called["ran"] = True
        return "ok"

    assert await observability.traced("flag_probe_off")(inner)() == "ok"
    assert called["ran"] is True, "关闭时也要正常执行（直通）"
    assert observability._wrapped == {}, "开关关闭时不应包装 —— 否则会建 run 并上报"
