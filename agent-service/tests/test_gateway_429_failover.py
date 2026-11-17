"""Agnes 非幂等生成请求遇 429 时的跨端点 failover 回归测试（不出网）。"""

import asyncio

import httpx
import pytest

import app.gateway.agnes as agnes
from app.gateway.agnes import AgnesGateway


class _RecordingHttp:
    def __init__(self, provider: str, responses: list[httpx.Response]) -> None:
        self.provider = provider
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def post(self, url: str, **kwargs) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class _Provider:
    def __init__(self, name: str, responses: list[httpx.Response]) -> None:
        self.name = name
        self.base_url = f"https://{name}.example/v1"
        self._client = _RecordingHttp(name, responses)


def _response(provider: str, status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=body,
        request=httpx.Request("POST", f"https://{provider}.example/v1/test"),
    )


def _gateway(*providers: _Provider) -> AgnesGateway:
    gateway = AgnesGateway.__new__(AgnesGateway)
    gateway.providers = {provider.name: provider for provider in providers}
    gateway.provider_names = [provider.name for provider in providers]
    gateway._session_provider = {}
    gateway._rr_index = 0
    gateway._rr_lock = asyncio.Lock()
    return gateway


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_image_429_switches_provider_and_rebinds_session():
    intl = _Provider("intl", [_response("intl", 429, {"error": "rate_limit"})])
    cn = _Provider("cn", [_response("cn", 200, {"data": [{"url": "https://cdn.example/frame.png"}]})])
    gateway = _gateway(intl, cn)
    gateway._session_provider["session-1"] = "intl"

    urls = await gateway.generate_image("一名剑客站在山巅", session_id="session-1")

    assert urls == ["https://cdn.example/frame.png"]
    assert len(intl._client.calls) == 1, "429 后应切换，不应继续消耗被限流端点的重试预算"
    assert len(cn._client.calls) == 1
    assert gateway._session_provider["session-1"] == "cn"


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_chat_429_uses_next_provider():
    intl = _Provider("intl", [_response("intl", 429, {"error": "rate_limit"})])
    cn = _Provider(
        "cn",
        [_response("cn", 200, {"choices": [{"message": {"content": "已切换端点"}}]})],
    )
    gateway = _gateway(intl, cn)
    gateway._session_provider["session-2"] = "intl"

    answer = await gateway.chat("返回一句话", session_id="session-2")

    assert answer == "已切换端点"
    assert len(intl._client.calls) == len(cn._client.calls) == 1
    assert gateway._session_provider["session-2"] == "cn"


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_single_provider_429_keeps_bounded_backoff_retries(monkeypatch):
    provider = _Provider(
        "intl",
        [_response("intl", 429, {"error": "rate_limit"}) for _ in range(3)],
    )
    gateway = _gateway(provider)
    sleeps: list[float] = []

    async def no_wait(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(agnes.asyncio, "sleep", no_wait)
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.generate_image("一名剑客站在山巅")

    assert len(provider._client.calls) == 3
    assert sleeps == [5, 10, 20]


@pytest.mark.real_gateway_method
@pytest.mark.asyncio
async def test_bad_request_does_not_switch_provider():
    intl = _Provider("intl", [_response("intl", 400, {"error": "invalid_parameter"})])
    cn = _Provider("cn", [_response("cn", 200, {"data": [{"url": "https://cdn.example/unused.png"}]})])
    gateway = _gateway(intl, cn)
    gateway._session_provider["session-3"] = "intl"

    with pytest.raises(httpx.HTTPStatusError):
        await gateway.generate_image("bad request", session_id="session-3")

    assert len(intl._client.calls) == 1
    assert len(cn._client.calls) == 0, "400 是请求/参数错误，换端点无法修复"
