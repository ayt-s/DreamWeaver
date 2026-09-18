"""`download()` 的完整性校验（2026-09-18 新增）。

为什么需要：agenes CDN 中途断流时，旧实现只把已收到的字节写完就**当成功返回**，
留下**残片**。实测真实产物里有两段残片：129~190 KB（正常 3~8 MB），
容器声明 107 帧、只能解出 11~12 帧 —— 下游 QC 于是拿十几个采样点（甚至 1 个）
下结论，用户看到的是「画面全黑/很糊」，指向「模型生成坏了」而不是「下载坏了」。

修法：按 Content-Length 核对实际写入字节数，不匹配即抛
`IncompleteDownloadError`（继承 OSError → 落在 `utils/retry.py` 的重试白名单里，
自动退避重下；重试耗尽才上抛，让调用方记「产物缺失」）。

⚠️ 测试直接调 `download.__wrapped__`（跳过 `@with_retry` 装饰层）：
否则失败用例会真的睡 5/15/45/90s 的退避。
"""
import gzip

import httpx
import pytest

from app.utils import media as media_mod


def _client_factory(handler):
    """把 media 里的 httpx.AsyncClient 换成带 MockTransport 的版本（不出网）。"""
    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(**kwargs)

    return factory


@pytest.mark.asyncio
async def test_truncated_body_raises(monkeypatch, tmp_path):
    def handler(request):
        # 声明 9999 字节，实际只给 1000 —— 断流残片
        return httpx.Response(200, content=b"x" * 1000,
                              headers={"content-length": "9999"})

    monkeypatch.setattr(media_mod.httpx, "AsyncClient", _client_factory(handler))
    dest = tmp_path / "seg.mp4"

    with pytest.raises(media_mod.IncompleteDownloadError) as ei:
        await media_mod.download.__wrapped__("http://mock/seg.mp4", dest)

    assert "1000/9999" in str(ei.value), f"要能看出差多少，实际: {ei.value!r}"
    # 残片会留在磁盘上（下一轮重试覆盖它），但错误必须抛出去让调用方知情
    assert dest.exists()


@pytest.mark.asyncio
async def test_complete_body_passes(monkeypatch, tmp_path):
    body = b"y" * 4096

    def handler(request):
        return httpx.Response(200, content=body,
                              headers={"content-length": str(len(body))})

    monkeypatch.setattr(media_mod.httpx, "AsyncClient", _client_factory(handler))
    dest = tmp_path / "seg.mp4"

    await media_mod.download.__wrapped__("http://mock/seg.mp4", dest)

    assert dest.read_bytes() == body


@pytest.mark.asyncio
async def test_compressed_response_is_not_treated_as_truncated(monkeypatch, tmp_path):
    """带 content-encoding 时 Content-Length 是**压缩后**长度，与解压后字节数不同。

    这时不能校验（否则会把正常响应误判成残片）—— 实测 agnes 产物是裸 mp4，
    但 CDN/代理加编码并非不可能，所以留这条护栏。
    这里刻意让「压缩长度 ≠ 解压长度」，去掉护栏就会抛 IncompleteDownloadError。
    """
    plain = b"z" * 5000
    compressed = gzip.compress(plain)
    assert len(compressed) != len(plain)

    def handler(request):
        return httpx.Response(200, content=compressed,
                              headers={"content-length": str(len(compressed)),
                                       "content-encoding": "gzip"})

    monkeypatch.setattr(media_mod.httpx, "AsyncClient", _client_factory(handler))
    dest = tmp_path / "seg.mp4"

    await media_mod.download.__wrapped__("http://mock/seg.mp4", dest)

    assert dest.read_bytes() == plain


@pytest.mark.asyncio
async def test_missing_content_length_is_not_treated_as_truncated(monkeypatch, tmp_path):
    """分块传输（无 Content-Length）没有可比对的基准，不该误判。"""
    def handler(request):
        return httpx.Response(200, content=b"a" * 500)

    monkeypatch.setattr(media_mod.httpx, "AsyncClient", _client_factory(handler))
    dest = tmp_path / "seg.mp4"

    await media_mod.download.__wrapped__("http://mock/seg.mp4", dest)

    assert dest.stat().st_size == 500
