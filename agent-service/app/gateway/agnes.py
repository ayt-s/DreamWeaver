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
from app.utils.observability import traced
from app.utils.prompting import (
    normalize_image_ratio,
    normalize_image_seed,
    normalize_image_size,
    normalize_video_size,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _with_retry(operation: Callable[[], Awaitable[httpx.Response]],
                      what: str, *, require_status: bool = False) -> httpx.Response:
    """带指数退避的重试包装（429/5xx/网络错误共用）。

    - 429 限流：5s→10s→20s→30s 封顶，最多 3 次
    - 5xx：2s→4s→8s→10s 封顶，最多 3 次
    - httpx.HTTPError（网络抖动）：固定 5s，最多 3 次

    ★ 2026-09-19 修（#8）：新增 `require_status`。
      改前形状：重试耗尽后**原样返回最后一个错误响应**（429/5xx 的 resp），
      调用方 `query_video` 直接 `resp.json()` 当正常结果用 —— 一次上游 5xx
      的 body 里若恰好带 `error` 键，`poller._check_task` 的失败判定就会命中，
      把**还在生成**的镜永久判失败（已付费产物被丢）。
      置 `require_status=True` 时，重试耗尽后抛 `httpx.HTTPStatusError`，
      把「查询失败」与「查询成功但状态为失败」彻底分开。
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
            # ⚠️ 别直接打印 {e}：httpx 传输层异常的 str() 常是空串，实测 2026-09-17
            # 日志出现「文本(intl) 请求异常: ，5s 后重试 (1/3)」—— 声称说了原因，
            # 实际什么都没说（视频路径已在 f84232c 用 _describe_transport_error 修过，
            # 这条重试日志当时漏了）。该函数保证带异常类型名。
            logger.error(
                f"{what} 请求异常（{_describe_transport_error(e)}），"
                f"5s 后重试 ({attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(5)
    # 理论不可达（HTTPError 已 raise）
    assert resp is not None
    # ★ 2026-09-19（#8）：重试耗尽且仍未拿到可接受状态 → 必须抛，不能把错误体当结果
    if require_status:
        resp.raise_for_status()
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


def _describe_transport_error(e: httpx.TransportError) -> str:
    """把传输层异常翻译成**可诊断**的中文。

    为什么要专门做这个（2026-09-15 实测踩到）：

    1. 原实现是 `f"网络异常 {e}"`，而 **httpx 的异常 `str(e)` 常常是空串** ——
       线上真实看到的错误就是 `视频提交所有 provider 都失败…：[intl] 网络异常 `
       （后面什么都没有），完全无法定位，为此白查了一轮「是不是网络/代理问题」。
    2. `httpx.ReadTimeout` 也是 `TransportError`。而**agnes 免费额度只有 RPM 限制**，
       平台排队时提交请求的慢响应会以读超时出现 —— 一律说成「网络异常」会把人带偏。

    所以这里必须带**异常类型名**，并按超时 / 连接 / 协议分开表述。
    """
    kind = type(e).__name__
    detail = str(e).strip()
    # 顺序有讲究：ConnectTimeout 同时是「超时」和「连接错误」，但它的病因是
    # **连不上**（网络/DNS/代理），不是排队等响应 —— 必须先判它，
    # 否则会把它说成「平台排队」，反而把人带偏。
    if isinstance(e, httpx.ConnectTimeout):
        hint = "连接超时（连不上平台：本机网络 / DNS / 代理，或平台不可达）"
    elif isinstance(e, httpx.ReadTimeout):
        hint = "读超时（等平台响应超时；平台排队/限流时常见，未必是网络故障）"
    elif isinstance(e, httpx.WriteTimeout):
        hint = "写超时（请求体没发完）"
    elif isinstance(e, httpx.PoolTimeout):
        hint = "连接池超时（并发请求挤满，非平台问题）"
    elif isinstance(e, httpx.TimeoutException):
        hint = "请求超时"
    elif isinstance(e, httpx.ConnectError):
        hint = "连接失败（本机网络 / DNS / 代理，或平台不可达）"
    elif isinstance(e, httpx.ProtocolError):
        hint = "协议错误（响应被截断或代理干扰）"
    else:
        hint = "传输层异常"
    return f"{hint} [{kind}]" + (f" {detail}" if detail else "（异常无文本信息）")


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
    # 批次 H：出口挂 LangSmith。默认关闭时直接透传（见 utils/observability.py）。
    # run_type=llm 让它出现在 LangSmith 的 LLM 视图里（文本模型才是真正的 LLM）
    @traced("agnes.chat", run_type="llm")
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

    @traced("agnes.chat_with_images", run_type="llm")
    async def chat_with_images(self, prompt: str, image_urls: list[str],
                               model: str | None = None, temperature: float = 0.0,
                               max_tokens: int = 800,
                               session_id: str | None = None) -> str:
        """多模态调用：文字 + 图片一起发给文本模型，返回纯文本。

        实测（2026-09-18）：agnes chat 吃 OpenAI 形态的多模态 content
        （`[{"type":"text",...},{"type":"image_url","image_url":{"url":...}}]`）——
        `/models` 里没有单独的视觉模型，`agnes-2.5-flash` 自己就能读图。

        ⚠️ **`max_tokens` 不能给小**：这个模型会先吐 `reasoning_content`，
        给小了推理就把额度吃光、`content` 变成空串（实测 13 张图里 7 张返回空，
        同样的图给足 token 重试就正常 —— 别把空答案当成「模型答不出来」）。
        """
        client = await self.pick_client(session_id=session_id)
        content: list[dict] = [{"type": "text", "text": prompt}]
        for u in image_urls:
            content.append({"type": "image_url", "image_url": {"url": u}})
        resp = await _with_retry(
            lambda: client._client.post("/chat/completions", json={
                "model": model or settings.text_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }),
            f"多模态({client.name})",
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ---------- 图像 ----------
    # run_type=tool：图像/视频提交是「工具调用」而非 LLM 补全，放 tool 视图更贴切
    @traced("agnes.generate_image", run_type="tool")
    async def generate_image(self, prompt: str,
                             model: str | None = None,
                             session_id: str | None = None,
                             size: str | None = None,
                             ratio: str | None = None,
                             seed: int | None = None,
                             reference_images: list[str] | None = None) -> list[str]:
        """同步调用图像 API，返回图片 URL 列表。多 provider 按 session 粘性路由。

        ⚠️ `size` / `ratio` **必须显式传**（2026-09-18 实测修正）：
        不传时服务端按 1:1 出图 —— 实测项目真实产物 18/18 全是 1024x1024 正方形，
        而视频链路是 16:9；首帧是画面的真正基底，正方形基底会被视频模型先重构图一次。
        传 `size="2K", ratio="16:9"` 实测得到 2624x1472（比例 1.783）。
        图片当前所有档位免费（官方 pricing），所以升档没有成本代价。

        `reference_images` 非空时走**图生图**（官方支持，实测返回 `/images/i2i/` 路径）：
        用来做「定点修正」——对已有图做定向修改（删掉多出来的主体等），
        这是文生图做不到的能力。⚠️ 取值范围是 `extra_body.image`，
        **不能放顶层 `image`**（实测顶层会 400，还抛上游 LLM Provider 错）。
        """
        client = await self.pick_client(session_id=session_id)
        refs = [str(u).strip() for u in (reference_images or []) if str(u).strip()]
        extra_body: dict[str, Any] = {"response_format": "url"}
        if refs:
            extra_body["image"] = refs
        payload: dict[str, Any] = {
            "model": model or settings.image_model,
            "prompt": prompt,
            "size": normalize_image_size(size or settings.image_size),
            "ratio": normalize_image_ratio(ratio, settings.default_aspect_ratio),
            # 官方要求 URL 输出走 extra_body（顶层 response_format 会 400）；
            # 不传时靠服务端默认，写死更可预期
            "extra_body": extra_body,
        }
        if seed is not None:
            # ⚠️ 别直接 int() 透传：seed 超出 [-1, 999] 会被上游 400 打挂整次出图
            #    （2026-09-18 实测），所以先归一到白名单区间。
            payload["seed"] = normalize_image_seed(seed)
        resp = await _with_retry(
            lambda: client._client.post("/images/generations", json=payload),
            f"图像({client.name})",
        )
        resp.raise_for_status()
        data = resp.json()
        urls: list[str] = [item["url"] for item in data["data"]]
        return urls

    @staticmethod
    def _resolve_video_size(model: str, requested: str | None) -> str:
        """解析视频分辨率档，并**按模型夹紧**。

        ⚠️ Flash（`agnes-video-2.5-flash`）**硬限 720P**，传别的档直接 400
        `size must be 720P` —— 所以无论调用方给什么，Flash 一律按 720P 发。
        非 Flash（`agnes-video-2.5`）按官方文档（2026-09-18）只吃 720P / 2K，
        此时用请求值 / 配置值（档位白名单见 `normalize_video_size`）。
        """
        if "flash" in (model or "").lower():
            return "720P"
        return normalize_video_size(requested or settings.video_size, "720P")

    # ---------- 视频 ----------
    @traced("agnes.submit_video", run_type="tool")
    async def submit_video(self, prompt: str, model: str | None = None,
                           seconds: str | None = None,
                           aspect_ratio: str | None = None,
                           mode: str = "text",
                           reference_images: list[str] | None = None,
                           session_id: str | None = None,
                           first_frame: str | None = None,
                           last_frame: str | None = None,
                           size: str | None = None,
                           seed: int | None = None) -> dict[str, Any]:
        """提交视频生成任务，返回包含 video_id / model_name / provider 的 dict。

        多 provider 场景：
        1. 从 provider 池按 session 粘性取一个 client 开始尝试
        2. 该 client 内退避重试 N 次（原有 429/503 退避逻辑）
        3. 如果同 client 全部失败，切到下一个 provider 再试一轮（粘附切换）
        4. 全部 provider 都失败则抛原错误
        返回 dict 额外带 provider 字段，poller 用它查询该视频。

        ## 三种模式与媒体字段互斥（官方 Generation Mode Rules，混用直接 400）

        | mode | 用途 | 必给 | **禁止** |
        |---|---|---|---|
        | text | 纯文生视频 | 无 | 所有媒体字段 |
        | keyframe | **首帧/尾帧锁定** | first_frame 或 last_frame | images / audios / videos |
        | reference | 参考图/音频 | images 或 audios 非空 | first_frame / last_frame |

        ★ keyframe 与 reference 的差别是**语义级**的（官方原文）：
        keyframe「尝试把输入图作为**实际第一帧**」；reference「可能**重新构图、重新计时**」。
        手里已有用户认可的首帧图时，keyframe 才是那条「视频从这张图长出来」的路。
        """
        resolved_model = model or settings.video_model_fast
        resolved_mode = mode if mode in ("text", "keyframe", "reference") else "text"
        refs = [str(u).strip() for u in (reference_images or []) if str(u).strip()]
        first = str(first_frame or "").strip()
        last = str(last_frame or "").strip()

        # 模式与媒体字段互斥：这里兜一层，免得上游多传一个字段就整段失败（一次 400 =
        # 白等一轮重试 + 该镜没有产物）。降级方向取「信息量更小但一定合法」的那一种。
        if resolved_mode == "keyframe" and not (first or last):
            resolved_mode = "reference" if refs else "text"
            logger.warning("keyframe 没有首/尾帧，降级为 %s（session=%s）", resolved_mode, session_id)
        if resolved_mode == "keyframe":
            if refs:
                logger.info("keyframe 模式不使用参考图（%d 张，官方禁止与 images 混用），已忽略",
                            len(refs))
        elif resolved_mode == "reference":
            if first or last:
                logger.info("reference 模式不接受 first_frame/last_frame，已忽略")
                first = last = ""
            if not refs:
                resolved_mode = "text"
        else:  # text
            refs = []
            first = last = ""

        payload: dict[str, Any] = {
            "model": resolved_model,
            "prompt": prompt,
            "mode": resolved_mode,
            "seconds": seconds or settings.default_seconds,
            "size": self._resolve_video_size(resolved_model, size),
            "aspect_ratio": aspect_ratio or settings.default_aspect_ratio,
            "n": 1,
        }
        if resolved_mode == "keyframe":
            if first:
                payload["first_frame"] = first
            if last:
                payload["last_frame"] = last
        elif resolved_mode == "reference":
            # 图生视频/参考模式：图片需公网可访问 URL
            payload["images"] = refs
        if seed is not None:
            payload["seed"] = int(seed)
        logger.warning("submit_video payload (session=%s): %s", session_id, _json_compact(payload))

        # 单 provider 内尝试次数（每个 provider 独立预算）
        attempts_per_provider = max(1, settings.video_submit_max_attempts)
        # ★ 2026-09-19 修（#11）：**读超时**的独立预算（默认 1 = 不重试）。
        #
        # 改前形状：读超时被当成普通 TransportError 处理 —— 与 429/503 一样
        # `attempt` 自增后 `continue`，于是同一个 payload 会被重复 POST 到
        # `/videos`（最多 6 次/provider × N 个 provider）。而 `/videos` 是**非幂等**
        # 的创建接口，读超时的语义是「请求已送达、平台可能已经在建任务，只是我没等到响应」
        # （`_describe_transport_error` 的文案也写着「读超时（等平台响应超时；平台排队/限流时常见）」）。
        # 后果链（每条都能在代码里对上）：
        #   1. 重试再建一个任务 → 平台侧出现**孤儿任务**：它的 video_id 从来没有
        #      返回给 agent，因此不写 `progress.submitted`（session_store.mark_submitted
        #      在 tools/video.py 里只记最终返回的那个），不进产物、不退款；
        #   2. HD 档按秒计费（$0.025–0.055/秒），孤儿任务照扣；
        #   3. 平台视频 RPM≈2/分钟，重复提交直接把提交配额吃满 → 同一会话后续段
        #      全部 429 退避，长任务被拖成超时失败。
        # 改法：按**错误类型**分流重试预算 ——
        #   - 读/写超时（ReadTimeout/WriteTimeout）：`timeout_attempts` 单独计数，
        #     默认 1 次即放弃（换 provider 也不重试：换账号同样会再建一个任务）；
        #   - 连接类（ConnectTimeout/ConnectError/PoolTimeout）：请求根本没送出去，
        #     重试安全，沿用原预算。
        timeout_attempts = max(1, settings.video_submit_timeout_max_attempts)
        connect_attempts = max(1, settings.video_submit_connect_max_attempts)
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
            # 读超时已经发生过 → 平台侧可能已建任务，**换 provider 也不该再提交**
            # （新账号建的是第二个任务，同样拿不到 id）。直接跳出整个 provider 循环。
            if timeout_attempts <= 0:
                logger.warning(
                    "视频提交：读超时后不再重试/不再切换 provider（避免非幂等重复提交），"
                    "session=%s last_reason=%s", session_id, last_reason)
                break
            attempt = 0
            while attempt < max(attempts_per_provider, timeout_attempts, connect_attempts):
                attempt += 1
                await get_video_gate().acquire()
                try:
                    resp = await client._client.post("/videos", json=payload)
                except httpx.TransportError as e:
                    is_read_timeout = isinstance(e, httpx.TimeoutException) and not isinstance(
                        e, httpx.ConnectTimeout)
                    if is_read_timeout:
                        timeout_attempts -= 1
                        budget = timeout_attempts
                    else:
                        connect_attempts -= 1
                        budget = connect_attempts
                    # 超时多半是平台排队（agnes 免费额度只有 RPM 限制），
                    # 用与 5xx 同量级的长退避；真·连接失败几次之后会如实抛出。
                    # 原实现对所有 TransportError 一律 `5 * attempt`（5~25s），
                    # 对「队列要几分钟才消化」的排队场景明显偏短。
                    base_wait = min(30 * attempt, 60) if is_read_timeout else 5 * attempt
                    wait = base_wait * (1 + random.uniform(-0.2, 0.2))
                    last_reason = f"[{provider_name}] {_describe_transport_error(e)}"
                    if budget <= 0:
                        break
                    # 格式串里不再重复 `[provider]`：last_reason 本身已带前缀，
                    # 写成 `视频提交[%s]%s` 会输出「视频提交[intl][intl] 读超时…」
                    logger.warning("视频提交%s，%.1fs 后重试 (%d/%d)",
                                   last_reason, wait, attempt,
                                   max(attempts_per_provider, timeout_attempts, connect_attempts))
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
                    if attempt >= attempts_per_provider:
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
                    if attempt >= attempts_per_provider:
                        break
                    # 同上：去重 provider 前缀
                    logger.warning("视频提交%s，%.1fs 后重试 (%d/%d)",
                                   last_reason, wait, attempt, attempts_per_provider)
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

    @traced("agnes.query_video", run_type="tool")
    async def query_video(self, video_id: str, model_name: str,
                          mode: str = "text",
                          provider_name: str | None = None) -> dict[str, Any]:
        """查询视频任务状态（实测 2026-09：返回含 progress 百分比、internal_status）。

        ⚠️ 实测确认：本版本 API 的 id/video_id/task_id 为同值（task_xxx 格式），
        统一用 video_id 参数查询 + 显式带 model_name，不要走 task_id 查询路径。

        多 provider 场景：query_video 按 provider_name 选择 client（由 poller 传入，
        保证和提交时的 provider 一致）。未指定时走 session 粘性（若可查）或第一个。

        重试策略：429 限流时指数退避（最多 3 次），5xx 服务端错误同样重试。

        ★ 2026-09-19 修（#8）：**必须校验 HTTP 状态**。
        改前形状只有 `return resp.json()` —— 既没有 `raise_for_status`，而 `_with_retry`
        重试耗尽后又会把最后一个错误响应（429/5xx）原样返回。于是上游一次
        401/404/5xx（key 轮换、账号不对、video_id 过期、平台抽风）产出的错误体
        会被当成**查询结果**交给 `poller._check_task`；只要该 body 里有 `error` 键
        （agnes 的错误体正是 `{"error": {...}}`），轮询器就命中「失败判定」分支，
        把这一镜**永久判失败**（future 置异常、从 pending 表摘除、已完成的产物被丢）。
        正确语义：查询侧的状态码异常 = **本次查询没成功**，调用方应重试/稍后再查，
        绝不能等价于「任务失败」。
        """
        client = await self.pick_client(provider_name=provider_name)
        resp = await _with_retry(
            lambda: client._client.get(
                "/agnesapi",
                params={"video_id": video_id, "model_name": model_name},
            ),
            f"视频查询({client.name}) {video_id}",
            require_status=True,
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