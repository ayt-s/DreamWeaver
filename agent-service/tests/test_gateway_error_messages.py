"""视频提交失败原因的**可诊断性**测试。

背景（2026-09-15 线上实测）：任务失败回调给 Java 的文案是

    视频提交所有 provider 都失败（已尝试 1 个 provider × 6 次重试）：[intl] 网络异常

—— 「网络异常」后面**什么都没有**。原因是原实现 `f"网络异常 {e}"` 里的
`str(e)` 是空串，而 httpx 的传输层异常经常不带文本信息。加上 `httpx.ReadTimeout`
也是 `TransportError`（agnes 免费额度只有 RPM 限制，平台排队导致的慢响应会以读超时
出现），于是一个「平台排队」被说成「网络异常」，白白排查了一轮网络/代理。

这组测试锁住：**任何传输层异常都必须给出可定位的文案**（带异常类型名 + 病因分档）。
"""

import httpx
import pytest

import app.gateway.agnes as agnes
from app.gateway.agnes import AgnesGateway, _describe_transport_error


def test_read_timeout_without_text_is_still_diagnosable():
    """★ 线上真实踩到的形状：ReadTimeout 且 str(e) 为空串。"""
    msg = _describe_transport_error(httpx.ReadTimeout(""))

    assert "读超时" in msg
    assert "ReadTimeout" in msg, "必须带异常类型名，否则无法定位"
    assert "平台排队" in msg, "读超时优先怀疑平台排队（agnes 只有 RPM 限制）"
    assert "（异常无文本信息）" in msg, "空文本要显式说明，不能留一个空尾缀"


def test_read_timeout_keeps_detail_text():
    msg = _describe_transport_error(httpx.ReadTimeout("timed out waiting for response"))

    assert "读超时" in msg
    assert "timed out waiting for response" in msg


def test_connect_timeout_is_not_reported_as_queueing():
    """ConnectTimeout 同时是超时和连接错误，病因是「连不上」，不能说是排队。"""
    msg = _describe_transport_error(httpx.ConnectTimeout(""))

    assert "连接超时" in msg
    assert "ConnectTimeout" in msg
    assert "排队" not in msg, "连接超时不是平台排队，说成排队会把人带偏"


def test_connect_error_keeps_detail():
    msg = _describe_transport_error(httpx.ConnectError("Connection refused"))

    assert "连接失败" in msg
    assert "Connection refused" in msg


def test_pool_and_protocol_errors_are_distinguished():
    pool = _describe_transport_error(httpx.PoolTimeout(""))
    protocol = _describe_transport_error(httpx.ProtocolError(""))

    assert "连接池超时" in pool
    assert "协议错误" in protocol


def test_unknown_transport_error_falls_back_but_stays_informative():
    msg = _describe_transport_error(httpx.TransportError("weird"))

    assert "传输层异常" in msg
    assert "TransportError" in msg


@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout(""),
    httpx.ConnectTimeout(""),
    httpx.WriteTimeout(""),
    httpx.PoolTimeout(""),
    httpx.ConnectError(""),
    httpx.ProtocolError(""),
    httpx.TransportError(""),
])
def test_never_produces_the_empty_online_message(exc):
    """★ 核心护栏：绝不能退化成「网络异常 」这种零信息文案。"""
    msg = _describe_transport_error(exc)

    assert msg.strip() != "网络异常"
    assert not msg.endswith("网络异常 "), "这正是线上无法定位的那条消息"
    assert len(msg) > 15, f"信息量不足: {msg!r}"
    assert type(exc).__name__ in msg


class _FakeHttpClient:
    """只提供 submit_video 会用到的 post。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def post(self, url, **kwargs):
        raise self._exc


class _FakeAgnesClient:
    def __init__(self, exc: Exception) -> None:
        self.name = "intl"
        self.base_url = "http://fake.agnes"
        self._client = _FakeHttpClient(exc)


class _NoopGate:
    """替掉真实 VideoSubmitGate —— 它默认要等 35s 间隔，测试里必须跳过。"""

    async def acquire(self) -> None:
        return None


@pytest.mark.real_gateway_method  # 故意驱动真实 submit_video；HTTP 客户端已换成替身，不出网
@pytest.mark.asyncio
async def test_submit_video_failure_message_is_diagnosable(monkeypatch):
    """★ 端到端护栏：驱动**真实** submit_video，断言最终抛出的文案可定位。

    这条消息经 recovery → 回调 → Java `error_message` → 任务卡片，
    是用户唯一能看到的东西；旧版本给的就是结尾什么都没有的「网络异常 」。
    """
    gateway = AgnesGateway.__new__(AgnesGateway)  # 绕开 __init__：不碰真实端点与凭据
    gateway.providers = {"intl": _FakeAgnesClient(httpx.ReadTimeout(""))}
    gateway.provider_names = ["intl"]
    gateway._session_provider = {}

    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())
    monkeypatch.setattr(agnes.settings, "video_submit_max_attempts", 1)

    with pytest.raises(RuntimeError) as ei:
        await gateway.submit_video(prompt="一只猫在草地上跑", session_id="s1")

    msg = str(ei.value)
    assert "所有 provider 都失败" in msg
    assert "[intl]" in msg
    assert "读超时" in msg, f"没有给出可定位的病因: {msg!r}"
    assert "ReadTimeout" in msg, f"没有给出异常类型名: {msg!r}"
    assert "网络异常" not in msg, "旧的零信息文案不该再出现"
