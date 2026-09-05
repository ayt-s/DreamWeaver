"""Agnes AI 模型网关（Phase 1 最小实现）。

封装 chat / 提交视频 / 查询视频三个端点，接口化以便未来换供应商。
视频相关约束（来自官方文档，已核实）：
- 异步任务，创建后必须用 video_id 查询（绝不用 task_id）
- 查询：GET /agnesapi?video_id=<ID>&model_name=<模型>
- seconds 为字符串 "4"~"12"；size 仅 "720P"；n 固定 1
"""
import asyncio
import json
import logging
import random
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


_AGNES_ERROR_CN = {
    "video_queue_full": "视频队列繁忙（平台队列已满），请稍后点击「重新生成」重试",
    "image_queue_full": "图片队列繁忙，请稍后重试",
    "rate_limit": "请求过于频繁，请稍后重试",
    "unauthorized": "API 密钥无效或已过期",
    "invalid_model": "模型参数不正确或当前不可用",
    "invalid_parameter": "请求参数不被平台接受",
    "insufficient_balance": "账户余额不足",
}


class VideoSubmitGate:
    """全局视频提交节流门：两次 /videos 提交至少间隔 interval_s（对齐 agnes 视频 RPM≈2/分）。

    所有会话（含重新生成任务）共用一个门，从根上避免多会话并发提交撞 429。
    异步锁按调用循环惰性创建（模块级单例，uvicorn 单循环内安全）。
    """

    def __init__(self, interval_s: float) -> None:
        self._interval = interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._last + self._interval - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._last = now
            logger.info("视频提交门放行（距上次 %.1fs）", now - (self._last - self._interval))


_gate: VideoSubmitGate | None = None


def get_video_gate() -> VideoSubmitGate:
    global _gate
    if _gate is None:
        _gate = VideoSubmitGate(settings.video_submit_interval_s)
    return _gate


def _extract_code(resp: httpx.Response) -> str:
    """从 agnes 错误体提取 code（如 video_queue_full），取不到返回空串。"""
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("code", ""))
    except Exception:
        pass
    return ""


def _json_compact(obj) -> str:
    """紧凑 JSON 序列化（日志用，确保中文可读）。"""
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def _describe_rejection(exception: httpx.HTTPStatusError, what: str) -> str:
    """解析 agnes 非 2xx 响应体，给出可读原因（含中文映射）。

    默认 raise_for_status 只保留状态码，真实原因（如 video_queue_full）会丢，
    任务卡片上只剩 '400 Bad Request'。这里把平台错误体解析出来并中文化。
    """
    code, message = "", ""
    try:
        body = exception.response.json()
        if isinstance(body, dict):
            code = str(body.get("code", ""))
            message = str(body.get("message", "") or "")
    except Exception:
        text = getattr(exception.response, "text", "") or ""
        if text:
            message = text[:120]
    status = exception.response.status_code
    if code in _AGNES_ERROR_CN:
        return f"{what}接口拒绝 ({status}): {_AGNES_ERROR_CN[code]}"
    if message:
        lower = message.lower()
        if "media must be a public" in lower:
            return f"{what}接口拒绝 ({status}): 参考图必须为公网 URL（本地上传/内网图片不支持），请改用历史作品或文生图产出"
        return f"{what}接口拒绝 ({status}): {message}"
    return f"{what}接口拒绝: HTTP {status}"


class AgnesClient:
    """单个 Agnes 端点的封装（1 provider = 1 base_url + 1 api_key + 1 AsyncClient）。

    多端点扩容场景下 AgnesGateway 会创建多个 AgnesClient 实例。
    """

    def __init__(self, base_url: str, api_key: str, name: str) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=self.headers, timeout=60.0
        )

    def build_request(self, method: str, url: str) -> httpx.Request:
        return self._client.build_request(method, url)

    async def close(self) -> None:
        await self._client.aclose()


class AgnesGateway:
    """多 Agnes 端点池 + 会话粘性路由 + 提交失败跨 provider failover。

    - 单 provider 时等价于原实现
    - 会话粘性：一个 session_id 的所有请求（chat/image/video/poll）走同一个 provider，
      保证 video 提交和后续 poll 使用同一账号
    - 提交失败：同 provider 内退避重试 N 次，耗尽后切下一个 provider 再试一轮
    - 查询/轮询：按 video_id 记录的 provider 路由（poller 传入）
    """

    def __init__(self) -> None:
        self.providers: dict[str, AgnesClient] = {}
        self.provider_names: list[str] = []
        for p in settings.agnes_providers:
            name = p["name"]
            self.providers[name] = AgnesClient(p["base_url"], p["api_key"], name)
            self.provider_names.append(name)
        if not self.provider_names:
            # 兜底：无 provider 时用 settings.agnes_base_url + api_key（可能为空串）
            self.providers["intl"] = AgnesClient(
                settings.agnes_base_url, settings.agnes_api_key, "intl"
            )
            self.provider_names = ["intl"]
        # 会话 → provider 粘性映射（新 session 首次调用时 round-robin 分配）
        self._session_provider: dict[str, str] = {}
        self._rr_index = 0
        self._rr_lock = asyncio.Lock()

    async def pick_client(self, session_id: str | None = None,
                          provider_name: str | None = None) -> AgnesClient:
        """按 provider_name 优先 → session 粘性 → round-robin 选 client。"""
        if provider_name and provider_name in self.providers:
            return self.providers[provider_name]
        if session_id:
            cached = self._session_provider.get(session_id)
            if cached and cached in self.providers:
                return self.providers[cached]
            async with self._rr_lock:
                name = self.provider_names[self._rr_index % len(self.provider_names)]
                self._rr_index += 1
                self._session_provider[session_id] = name
                return self.providers[name]
        # 无 session_id（chat/novel 等共享调用）：直接 round-robin 但不写入粘性
        async with self._rr_lock:
            name = self.provider_names[self._rr_index % len(self.provider_names)]
            self._rr_index += 1
        return self.providers[name]

    def bind_session(self, session_id: str, provider_name: str) -> None:
        """显式绑定 session 到 provider（failover 切换后调用）。"""
        self._session_provider[session_id] = provider_name
        logger.info("session %s 粘附切换到 provider %s", session_id, provider_name)

    # ---------- 文本 ----------
    async def chat(self, prompt: str, model: str | None = None,
                   temperature: float = 0.2, max_tokens: int = 4096,
                   session_id: str | None = None) -> str:
        """调用文本模型，返回纯文本内容（Phase 1 用简单形式，结构化输出后续加）。

        多 provider 场景：按 session_id 粘性路由（同一 session 内 chat/storyboard/
        script/image/video 都用同一个 provider），无 session_id 时 round-robin。
        """
        client = await self.pick_client(session_id=session_id)
        resp = await _with_retry(
            lambda: client._client.post("/chat/completions", json={
                "model": model or settings.text_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }),
            f"文本({client.name})",
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ---------- 图像 ----------
    async def generate_image(self, prompt: str,
                             model: str | None = None,
                             session_id: str | None = None) -> list[str]:
        """同步调用图像 API，返回图片 URL 列表。多 provider 按 session 粘性路由。"""
        client = await self.pick_client(session_id=session_id)
        resp = await _with_retry(
            lambda: client._client.post("/images/generations", json={
                "model": model or settings.image_model,
                "prompt": prompt,
            }),
            f"图像({client.name})",
        )
        resp.raise_for_status()
        data = resp.json()
        urls: list[str] = [item["url"] for item in data["data"]]
        return urls

    # ---------- 视频 ----------
    async def submit_video(self, prompt: str, model: str | None = None,
                           seconds: str | None = None,
                           aspect_ratio: str | None = None,
                           mode: str = "text",
                           reference_images: list[str] | None = None,
                           session_id: str | None = None) -> dict[str, Any]:
        """提交视频生成任务，返回包含 video_id / model_name / provider 的 dict。

        多 provider 场景：
        1. 从 provider 池按 session 粘性取一个 client 开始尝试
        2. 该 client 内退避重试 N 次（原有 429/503 退避逻辑）
        3. 如果同 client 全部失败，切到下一个 provider 再试一轮（粘附切换）
        4. 全部 provider 都失败则抛原错误
        返回 dict 额外带 provider 字段，poller 用它查询该视频。
        """
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
        logger.warning("submit_video payload (session=%s): %s", session_id, _json_compact(payload))

        # 单 provider 内尝试次数（每个 provider 独立预算）
        attempts_per_provider = max(1, settings.video_submit_max_attempts)
        # 决定起点：如果有 session 粘性走粘性 provider 开始，否则 round-robin
        starting_index = 0
        if session_id and session_id in self._session_provider:
            idx = self.provider_names.index(self._session_provider[session_id])
            starting_index = idx
        # 收集所有 provider 按起点顺序排列
        rotated = self.provider_names[starting_index:] + self.provider_names[:starting_index]
        last_reason = "未知错误"
        for provider_name in rotated:
            client = self.providers[provider_name]
            for attempt in range(1, attempts_per_provider + 1):
                await get_video_gate().acquire()
                try:
                    resp = await client._client.post("/videos", json=payload)
                except httpx.TransportError as e:
                    last_reason = f"[{provider_name}] 网络异常 {e}"
                    if attempt == attempts_per_provider:
                        break
                    wait = 5 * attempt
                    logger.warning("视频提交[%s]网络异常，%ds 后重试 (%d/%d)",
                                   provider_name, wait, attempt, attempts_per_provider)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 200:
                    data = resp.json()
                    logger.info("视频提交成功 provider=%s video_id=%s session=%s",
                                provider_name, data["id"], session_id)
                    return {
                        "video_id": data["id"],
                        "model_name": payload["model"],
                        "provider": provider_name,
                    }

                if resp.status_code == 429:
                    # 限流：指数退避，RPM≈2 时稍等即可。封顶 30s + ±20% 抖动防并发会话同时退避完撞墙。
                    # 多 provider 场景下撞墙就切账号，不依赖加长退避。
                    base_wait = min(5 * (2 ** (attempt - 1)), 30)
                    wait = base_wait * (1 + random.uniform(-0.2, 0.2))
                    last_reason = f"[{provider_name}] 平台限流(429)"
                    if attempt == attempts_per_provider:
                        break
                    logger.warning("视频提交[%s]被限流，%.1fs 后重试 (%d/%d)",
                                   provider_name, wait, attempt, attempts_per_provider)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    # 服务端繁忙：video_queue_full → 长等待（队列可能要几分钟才消化），封顶 60s + 抖动
                    code = _extract_code(resp)
                    queue_full = code == "video_queue_full" or "queue" in code.lower()
                    base_wait = min(30 * attempt, 60) if queue_full else min(10 * attempt, 60)
                    wait = base_wait * (1 + random.uniform(-0.2, 0.2))
                    last_reason = f"[{provider_name}] 服务端 {resp.status_code} ({code or 'server error'})"
                    if attempt == attempts_per_provider:
                        break
                    logger.warning("视频提交[%s]%s，%.1fs 后重试 (%d/%d)",
                                   provider_name, last_reason, wait, attempt, attempts_per_provider)
                    await asyncio.sleep(wait)
                    continue

                # 4xx：参数/模式/权限错误 → 立即失败，不切换 provider（换账号也救不了）
                raise RuntimeError(_describe_rejection(
                    httpx.HTTPStatusError(
                        f"视频提交[{provider_name}] {resp.status_code}",
                        request=client.build_request("POST", str(client.base_url) + "/videos"),
                        response=resp,
                    ),
                    "视频",
                ))

            logger.warning("provider %s 视频提交 %d 次尝试耗尽，切换到下一 provider",
                           provider_name, attempts_per_provider)

        raise RuntimeError(
            f"视频提交所有 provider 都失败（已尝试 {len(rotated)} 个 provider × "
            f"{attempts_per_provider} 次重试）：{last_reason}"
        )

    async def query_video(self, video_id: str, model_name: str,
                          mode: str = "text",
                          provider_name: str | None = None) -> dict[str, Any]:
        """查询视频任务状态（实测 2026-09：返回含 progress 百分比、internal_status）。

        ⚠️ 实测确认：本版本 API 的 id/video_id/task_id 为同值（task_xxx 格式），
        统一用 video_id 参数查询 + 显式带 model_name，不要走 task_id 查询路径。

        多 provider 场景：query_video 按 provider_name 选择 client（由 poller 传入，
        保证和提交时的 provider 一致）。未指定时走 session 粘性（若可查）或第一个。

        重试策略：429 限流时指数退避（最多 3 次），5xx 服务端错误同样重试。
        """
        client = await self.pick_client(provider_name=provider_name)
        resp = await _with_retry(
            lambda: client._client.get(
                "/agnesapi",
                params={"video_id": video_id, "model_name": model_name},
            ),
            f"视频查询({client.name}) {video_id}",
        )
        return resp.json()

    async def close(self) -> None:
        for client in self.providers.values():
            await client.close()


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