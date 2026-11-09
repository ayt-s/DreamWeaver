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


# ============================================================================
# 读超时的跨 provider 预算（2026-09-24 策略变更）
# ============================================================================
# 背景：agnes 额度免费 → 孤儿任务的**金钱成本为 0**，而「整段彻底没产物」代价更大。
# 所以读超时不再全局一次即放弃，而是**每个 provider 各 1 次**；同 provider 仍不重试。

class _OkResp:
    status_code = 200

    @staticmethod
    def json():
        return {"id": "task_fake_ok"}


class _RecordingHttp:
    """记录每次 post 的 kwargs，行为可编程（抛异常 / 返回成功）。"""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls: list[dict] = []

    async def post(self, url, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


class _FakeProvider:
    def __init__(self, name: str, behaviour) -> None:
        self.name = name
        self.base_url = f"http://fake.{name}"
        self._client = _RecordingHttp(behaviour)


def _gateway(*providers) -> AgnesGateway:
    gw = AgnesGateway.__new__(AgnesGateway)
    gw.providers = {p.name: p for p in providers}
    gw.provider_names = [p.name for p in providers]
    gw._session_provider = {}
    return gw


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_read_timeout_falls_over_to_next_provider(monkeypatch):
    """读超时后换另一个账号再试 1 次（免费额度下优先保住出片）。"""
    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())
    monkeypatch.setattr(agnes.settings, "video_submit_max_attempts", 1)
    monkeypatch.setattr(agnes.settings, "video_submit_timeout_max_attempts", 1)

    a = _FakeProvider("intl", httpx.ReadTimeout(""))
    b = _FakeProvider("cn", _OkResp())
    gw = _gateway(a, b)
    gw._session_provider["s2"] = "intl"  # 粘性从 intl 起

    out = await gw.submit_video(prompt="一只猫", session_id="s2")

    assert out["provider"] == "cn", "应换到另一个 provider 并成功"
    assert len(a._client.calls) == 1, "同 provider 不许重试（易撞同一队列 / 重复建任务）"
    assert len(b._client.calls) == 1


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_read_timeout_still_no_same_provider_retry(monkeypatch):
    """只有一个 provider 时，读超时必须只提交一次。"""
    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())
    monkeypatch.setattr(agnes.settings, "video_submit_max_attempts", 1)
    monkeypatch.setattr(agnes.settings, "video_submit_timeout_max_attempts", 1)

    a = _FakeProvider("intl", httpx.ReadTimeout(""))
    gw = _gateway(a)

    with pytest.raises(RuntimeError):
        await gw.submit_video(prompt="一只猫", session_id="s3")

    assert len(a._client.calls) == 1, "同 provider 一次读超时即放弃，绝不重复 POST"


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_submit_uses_relaxed_read_timeout(monkeypatch):
    """提交接口单独放宽读超时（默认 180s），不再吃 client 全局的 60s。"""
    monkeypatch.setattr(agnes, "get_video_gate", lambda: _NoopGate())
    monkeypatch.setattr(agnes.settings, "video_submit_max_attempts", 1)

    a = _FakeProvider("intl", _OkResp())
    gw = _gateway(a)
    await gw.submit_video(prompt="一只猫", session_id="s4")

    assert a._client.calls[0].get("timeout") == agnes.settings.video_submit_read_timeout_s
    assert agnes.settings.video_submit_read_timeout_s >= 120, "60s 对排队场景偏短"
