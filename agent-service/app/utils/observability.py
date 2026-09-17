"""LLM 出口的 LangSmith tracing（P0-2 架包，批次 H）。

## 口径：只做「架包」

按已拍板决定：引入依赖 + 出口接线 + 开关，**暂不建评测集**
（Datasets / Experiments 后面有需要再扩展）。

## 为什么必须在出口手动包一层

本项目 LLM 调用是**裸 httpx**（`gateway/agnes.py`）。LangGraph 的自动 tracing
只覆盖**图结构事件**（节点 / 边），**捕获不到节点内部的 httpx 调用** ——
所以提示词 / 模型 / 重试这些细节只能自己在出口包。

顺带一个实测事实（别照着写错文档）：`langsmith` 模块本身**已经被 langgraph →
langchain-core 间接导入**了，所以「关闭时不 import」是不成立的。
关闭时省下的是**包装与客户端初始化**（`traceable` + 客户端 + 后台线程），
这才是有成本的部分。

## 硬约束

1. **默认关闭，关闭时直通**：不触碰 langsmith 的任何功能（不构造 `traceable`、
   不初始化客户端）
2. **没配 key 也视为关闭**：否则每次调用都会尝试上报并刷错误日志（纯噪声）
3. **任何失败都降级为直通**：可观测性挂掉绝不能拖垮生成主流程
4. **不用 `langchain_openai.ChatOpenAI` 替换 gateway**：gateway 有 round-robin +
   跨 provider failover + 429/503 退避，是全项目 LLM 出口，推倒重来风险远大于收益
"""

from __future__ import annotations

import functools
import logging
import os
import re

from app.config import settings

logger = logging.getLogger(__name__)

#: 惰性包装缓存：key 用 `模块.限定名`（比 id() 稳，不会被回收复用的 id 撞上）
_wrapped: dict[str, object] = {}

#: 包装失败的告警只打一次，避免每个请求刷一行
_warned = False

#: key 形状告警也只打一次（与 _warned 分开：形状对但包装失败、或反之，各告一次）
_warned_bad_key = False


def enabled() -> bool:
    """是否真的要上报。

    每次调用都重新读 settings / 环境变量（而不是模块级常量）——
    测试要能随时开关，运行期改配置也能立刻生效。
    """
    if not bool(getattr(settings, "langsmith_tracing", False)):
        return False
    # 没 key 就当作关闭：langsmith 认 LANGSMITH_API_KEY，也兼容旧的 LANGCHAIN_API_KEY
    return bool(os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY"))


#: 所有 run 都带的标签：在 LangSmith 里一眼筛出「这个项目、这个服务」的调用。
#: 参考同机 `YanQue-AI` 的做法 —— 它用 run_name/tags/metadata 把图调用与其它库区分开，
#: 否则列表里全是笼统的节点名，认不出哪条属于谁。
DEFAULT_TAGS = ["dreamweaver", "agent-service"]


def traced(name: str, run_type: str = "llm", tags: list[str] | None = None):
    """把 LLM 出口函数挂到 LangSmith 上（关闭时直通）。

    用法：`@traced("agnes.chat")` 加在 `gateway/agnes.py` 的出站方法上。

    `tags` 不传时**自动**用 run 名的后缀（`agnes.chat` → `chat`）：每个出口天然可筛
    （在 LangSmith 里按 `chat` / `submit_video` 过滤），不必在每个调用点手写一遍
    —— 手写的地方多了必然漏。
    """
    auto_tag = [name.rsplit(".", 1)[-1]] if "." in name else []
    all_tags = [*DEFAULT_TAGS, *auto_tag, *(tags or [])]

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not enabled():
                return await fn(*args, **kwargs)
            key = f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', name)}"
            wrapped = _wrapped.get(key)
            if wrapped is None:
                wrapped = _try_wrap(fn, name, run_type, all_tags)
                _wrapped[key] = wrapped
            return await wrapped(*args, **kwargs)

        return wrapper

    return decorator


# `lsv2_pt_<32 位 hex>_<10 位>`（个人令牌）或 `lsv2_sk_...`（服务令牌）。
#
# ⚠️ 为什么专门校验形状（2026-09-17 实测）：从 LangSmith 页面抄 key 时**少抄一个字符**
# （50 位而不是 51 位），结果**读 `/sessions` 与写 `/runs/multipart` 一律 403**，
# 而 langsmith SDK 的报错只说 `403 Forbidden`，不带任何「key 不对」的线索 ——
# 排查方向很容易被带偏到网络/代理/区域（我确实先查了三个区域域名）。
# 所以形状不对就**大声告警**，一眼定位。
_KEY_SHAPE = re.compile(r"^lsv2_[a-z]{2}_[0-9a-f]{32}_\w{10}$")


def key_shape_ok(key: str) -> bool:
    """key 形状是否符合 LangSmith 令牌格式（不校验有效性，只挡「抄错/截断」）。"""
    return bool(_KEY_SHAPE.match(key or ""))


def _warn_bad_key_shape(key: str) -> None:
    global _warned_bad_key
    if not key or key_shape_ok(key):
        return
    if not _warned_bad_key:
        _warned_bad_key = True
        logger.warning(
            "LANGSMITH_API_KEY 形状可疑（长度 %d，应为 51：lsv2_pt_<32位hex>_<10位>）。"
            "形状不对时 LangSmith 对读和写一律返回 403，别去查网络/区域/权限：%s…",
            len(key), key[:13],
        )


def _try_wrap(fn, name: str, run_type: str, tags: list[str] | None = None):
    """惰性包一层 `langsmith.traceable`；包不上就退回原函数。

    ⚠️ 为什么还要 `tracing_context(enabled=True)`（2026-09-17 实测，代价是一整轮排查）：

    `traceable` 到底记不记，取决于 **SDK 自己**读 `LANGSMITH_TRACING` 的结论，而它不认
    `1` 这种写法。于是出现「我们自己的开关说开、SDK 说没开、`traceable` **静默直通**」：
    网页上一条 run 都没有，日志里也不报错 —— 看起来接好了，其实什么都没上报。
    既然我们已经判定「开」，就在调用处**显式打开**，两边不可能再分歧。

    顺带把 config 里声明的 key / endpoint / project 真正传下去 —— 它们此前只声明、
    从未传给 SDK（SDK 靠同名环境变量兜住了，但字段本身是死的，会误导下一个人）。
    """
    global _warned
    try:
        from langsmith import Client, traceable
        from langsmith.run_helpers import tracing_context

        key = getattr(settings, "langsmith_api_key", "") or os.getenv("LANGSMITH_API_KEY", "")
        _warn_bad_key_shape(key)
        client = Client(
            api_key=key or None,
            api_url=settings.langsmith_endpoint or None,
        )
        wrapped = traceable(
            name=name, run_type=run_type, client=client,
            project_name=settings.langsmith_project or None,
            tags=list(tags or []),
        )(fn)
    except Exception as exc:  # noqa: BLE001 —— 可观测性不能影响主流程
        if not _warned:
            _warned = True
            logger.warning("LangSmith 包装失败，已降级为不上报（只告警一次）: %s", exc)
        return fn

    @functools.wraps(fn)
    async def _call(*args, **kwargs):
        with tracing_context(enabled=True):
            return await wrapped(*args, **kwargs)

    return _call


def reset_cache() -> None:
    """清掉惰性包装缓存（测试用：让下一次调用重新走包装路径）。"""
    _wrapped.clear()
