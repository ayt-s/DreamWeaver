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

import logging

import pytest

from app.utils import observability


@pytest.mark.asyncio
async def test_our_flag_on_forces_sdk_tracing(monkeypatch):
    monkeypatch.setattr(observability.settings, "langsmith_tracing", True)
    monkeypatch.setenv("LANGSMITH_TRACING", "1")  # ← SDK 不认的写法，正是踩到的那个
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake_for_test")
    # 别真往 LangSmith 打：丢给一个必然拒绝的本地端口。
    # ⚠️ 必须同时改 settings（_try_wrap 用的是 settings.langsmith_endpoint，环境变量会被它覆盖）
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(observability.settings, "langsmith_endpoint", "http://127.0.0.1:9")
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


# ------------------------------------------------- 令牌形状（抄错一位的护栏）


def test_key_shape_guard_catches_transcription_typo():
    """★ 回归护栏：抄 key 少一位（50 而不是 51）时必须被判出来。

    2026-09-17 实测：这种 key 在 LangSmith 读（`/sessions`）和写（`/runs/multipart`）
    一律 403，而 SDK 只回一句 `403 Forbidden`，没有任何「key 不对」的线索 ——
    我因此先跑去查了网络、代理、三个区域域名（方向全错）。

    形状的权威来源：同机另一个项目 `YanQue-AI/.env` 里能用的 key 就是
    `lsv2_pt_<32位hex>_<10位>` 共 51 位。
    """
    good = "lsv2_pt_" + "a1b2c3d4e5f60718293a4b5c6d7e8f90" + "_0123456789"
    assert len(good) == 51, "自己先把样例长度算对，否则这条测试没意义"
    assert observability.key_shape_ok(good)

    assert not observability.key_shape_ok(good[:-1]), "少一位（正是踩到的形态）应被判出"
    assert not observability.key_shape_ok(good + "x"), "多一位也应被判出"
    assert not observability.key_shape_ok("")
    assert not observability.key_shape_ok(None)
    assert not observability.key_shape_ok("lsv2_pt_" + "z" * 32 + "_0123456789"), "非 hex"


def test_bad_key_shape_warns_once(caplog):
    """形状告警只打一次（否则每个请求刷一行日志）。"""
    observability._warned_bad_key = False
    with caplog.at_level(logging.WARNING):
        observability._warn_bad_key_shape("lsv2_pt_short")
        observability._warn_bad_key_shape("lsv2_pt_short")

    hits = [r for r in caplog.records if "形状可疑" in r.getMessage()]
    assert len(hits) == 1, f"应只告警一次，实际 {len(hits)} 次"
    assert "403" in hits[0].getMessage(), "告警要说清后果（403），否则看不懂为什么要管它"
