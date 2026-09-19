"""配额秒数（#33）两条半链路的直接证据（2026-09-19 修）。

## 缺陷
Java 的 `NotifyRequest.shot_seconds` 声明了、`NotifyServiceImpl` 也真的拿它去
`apiQuotaMapper.increment(...)`（`shot_seconds != null ? 它 : DEFAULT_SHOT_SECONDS=5`），
而 agent 侧**从来没发过这个字段** ⇒ 每条回调都按默认 5 秒记账：
6 段 × 5 秒的任务真实 30 秒只记 5 秒（少 6 倍）、1 段 12 秒只记 5 秒（少一半多），
`api_quota.used_seconds` 与实际生成时长无关。

## 本用例证明（两条半链路）
1. `total_shot_seconds()` 真的按分段秒数求和，且**取不到时刻意返回 None**
   （= 不发字段、保留 Java 默认行为）—— 不是 0：0 会把配额页写成"消耗 0 秒"，比默认 5 秒更错；
2. `notify_java_completion()` 发出的 payload 里**确实带** `shot_seconds`。

⚠️ 注意 `notify_java_completion` 在 `settings.java_notify_url` 未配置时会提前 return
（本机测试环境就是如此），所以下面的用例必须先把 URL 打桩，否则测的是一场空。
"""
import pytest

from app.callback.java_notify import total_shot_seconds


def test_total_shot_seconds_sums_storyboard():
    assert total_shot_seconds([{"seconds": "5"}, {"seconds": "5"}, {"seconds": "6"}], None) == 16


def test_total_shot_seconds_prefers_segments():
    """画布模式以 segments 为准（段里才带真实秒数）。"""
    assert total_shot_seconds([{"seconds": "9"}], [{"seconds": 4}, {"seconds": 4}]) == 8


def test_total_shot_seconds_returns_none_when_unknown():
    """取不到 → None（刻意不是 0）：None = 不发字段，保留 Java 的 DEFAULT_SHOT_SECONDS。"""
    assert total_shot_seconds(None, None) is None
    assert total_shot_seconds([], []) is None


@pytest.mark.asyncio
async def test_notify_payload_carries_shot_seconds(monkeypatch):
    """回调 payload 必须带 shot_seconds（这是 Java 配额唯一的数据来源）。"""
    from app.callback import java_notify as jn

    sent: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"code": 0}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            sent["payload"] = json or {}
            return _Resp()

    client = _Client()
    # URL 未配置会提前 return —— 必须先打桩，否则这场用例什么都没测到
    monkeypatch.setattr(jn.settings, "java_notify_url", "http://127.0.0.1:9/notify", raising=False)
    monkeypatch.setattr(jn.httpx, "AsyncClient", lambda *a, **kw: client)
    # 客户端无关：模块若持有单例 client，也一并换掉
    for attr in ("_client", "_CLIENT", "client", "_http"):
        if hasattr(jn, attr):
            monkeypatch.setattr(jn, attr, client)

    await jn.notify_java_completion(
        session_id="quota-test", status="completed",
        shot_seconds=total_shot_seconds([{"seconds": "5"}] * 6, None),
    )
    assert sent.get("payload"), "回调没发出（URL 打桩失败？）"
    assert sent["payload"].get("shot_seconds") == 30, sorted(sent["payload"].keys())
