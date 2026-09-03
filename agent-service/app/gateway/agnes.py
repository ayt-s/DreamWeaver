"""Agnes AI 模型网关（Phase 1 最小实现）。

封装 chat / 提交视频 / 查询视频三个端点，接口化以便未来换供应商。
视频相关约束（来自官方文档，已核实）：
- 异步任务，创建后必须用 video_id 查询（绝不用 task_id）
- 查询：GET /agnesapi?video_id=<ID>&model_name=<模型>
- seconds 为字符串 "4"~"12"；size 仅 "720P"；n 固定 1
"""
import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, TypeVar

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _with_retry(operation: Callable[[], Awaitable[httpx.Response]],
                      what: str) -> httpx.Response:
    """带指数退避的重试包装（429/5xx/网络错误共用）。

    - 429 限流：5s→10s→20s→30s 封顶，最多 3 次
    - 5xx：2s→4s→8s→10s 封顶，最多 3 次
    - httpx.HTTPError（网络抖动）：固定 5s，最多 3 次
    """
    max_retries = 3
    resp: httpx.Response | None = None
    for attempt in range(max_retries):
        try:
            resp = await operation()
            if resp.status_code == 429:
                wait = min(5 * (2 ** attempt), 30)
                logger.warning(f"{what} 429 限流，{wait}s 后退避重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = min(2 * (2 ** attempt), 10)
                logger.warning(f"{what} {resp.status_code} 服务端错误，{wait}s 后退避重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait)
                continue
            return resp
        except httpx.HTTPError as e:
            if attempt == max_retries - 1:
                raise
            logger.error(f"{what} 请求异常: {e}，5s 后重试 ({attempt + 1}/{max_retries})")
            await asyncio.sleep(5)
    # 理论不可达（HTTPError 已 raise）
    assert resp is not None
    return resp


class AgnesGateway:
    def __init__(self) -> None:
        self.base_url = settings.agnes_base_url
        self.headers = settings.headers
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=self.headers, timeout=60.0
        )

    # ---------- 文本 ----------
    async def chat(self, prompt: str, model: str | None = None,
                   temperature: float = 0.2, max_tokens: int = 4096) -> str:
        """调用文本模型，返回纯文本内容（Phase 1 用简单形式，结构化输出后续加）。"""
        resp = await self._client.post("/chat/completions", json={
            "model": model or settings.text_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ---------- 视频 ----------
    async def submit_video(self, prompt: str, model: str | None = None,
                           seconds: str | None = None,
                           aspect_ratio: str | None = None,
                           mode: str = "text",
                           reference_images: list[str] | None = None) -> dict[str, Any]:
        """提交视频生成任务，返回包含 video_id / model_name 的 dict。"""
        payload: dict[str, Any] = {
            "model": model or settings.video_model_fast,
            "prompt": prompt,
            "mode": mode,
            "seconds": seconds or settings.default_seconds,
            "size": "720P",
            "aspect_ratio": aspect_ratio or settings.default_aspect_ratio,
            "n": 1,
        }
        if reference_images:
            # 图生视频/参考模式：图片需公网可访问 URL
            payload["images"] = reference_images
        resp = await _with_retry(
            lambda: self._client.post("/videos", json=payload),
            "视频提交",
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "video_id": data["id"],
            "model_name": payload["model"],
        }

    async def query_video(self, video_id: str, model_name: str,
                          mode: str = "text") -> dict[str, Any]:
        """查询视频任务状态（实测 2026-09：返回含 progress 百分比、internal_status）。

        ⚠️ 实测确认：本版本 API 的 id/video_id/task_id 为同值（task_xxx 格式），
        统一用 video_id 参数查询 + 显式带 model_name，不要走 task_id 查询路径。

        重试策略：429 限流时指数退避（最多 3 次），5xx 服务端错误同样重试。
        """
        resp = await _with_retry(
            lambda: self._client.get(
                "/agnesapi",
                params={"video_id": video_id, "model_name": model_name},
            ),
            f"视频查询 {video_id}",
        )
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()


# ---------- 懒加载单例 ----------
# 模块顶层不实例化：httpx.AsyncClient 创建在部分网络环境下耗时数秒（实测 import 3.7s），
# 且测试需要替换 gateway。用一个零开销的代理对象占位 `gateway` 符号，
# 真正首次调用方法时才创建真实 client —— 保持所有 `from ... import gateway` 写法不变。
_gateway: "AgnesGateway | None" = None


def get_gateway() -> "AgnesGateway":
    """获取网关单例（懒加载，首次访问才建 AsyncClient）。"""
    global _gateway
    if _gateway is None:
        _gateway = AgnesGateway()
    return _gateway


class _LazyGatewayProxy:
    """占位代理：任何属性访问（方法调用）首次触发时创建真实 gateway。"""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_gateway(), name)


gateway = _LazyGatewayProxy()  # 顶层零开销；import 不会触发 AsyncClient