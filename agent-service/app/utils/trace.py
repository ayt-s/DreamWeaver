"""极简轨迹（trace）写入助手 —— 批次 C1/C2。

## 为什么只留三个字段

state 每个 checkpoint 都会**全量**写进 Redis 快照（`main.py` 的
`astream(stream_mode="values")` → `session_store.save_state`）。原先的 trace 条目是
「每次工具调用的完整审计记录」（`tool_name` / `params` / `result` / `latency_ms` /
`timestamp` / `retry_count`），而 `params` 里**含 `prompt_en` 正文** ——
storyboard 本身已经存了同一份提示词，同一段文本在快照里出现两遍。

已拍板口径（P0-4 trace 走 **A+B 混合**）：
- **LLM 调用级细节交给 LangSmith**（`app/utils/observability.py`，默认关闭、零开销）
- **state 里只留 `{node, status, elapsed_ms}`**，给 `GET /v1/tasks/{id}` 画轨迹条

⚠️ **这不是「省 Redis」的优化，别拿体积当理由**：实测降幅有限
（storyboard 每镜的 `prompt`/`prompt_en`/`camera_spec` 与 trace 量级相当）。
真正理由是**链路完整性 + 可观测性** —— 原来只有 3 个节点埋点，链路根本没连起来，
而且没有任何 API 返回、前端也不渲染它，等于纯只写字段。

## 约定

- 条目**只能**由本模块构造 → 结构上不可能多出第四个键（防「顺手加个 prompt」）
- `node` 允许带序号后缀：`video_generator#2`。10 镜任务若把每条都塌缩成
  一行 `video_generator`，时间线就只剩一根长条，看不出卡在第几镜 ——
  序号是时间线可用的前提。用 `shot()` 生成。
"""

from __future__ import annotations

import time

#: trace 条目允许的键。测试直接断言它，防止有人把提示词正文塞回来。
TRACE_KEYS: tuple[str, ...] = ("node", "status", "elapsed_ms")

#: 条目上限（Task C2）。10 镜任务每镜至少 2 条（提交 + 完成）+ 前期节点 +
#: 修复轮次，理论上限约 60~80；200 是安全余量，防异常路径下无界增长。
TRACE_MAX = 200

#: 状态取值字典（刻意收窄，前端按它上色/选图标）
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_REUSED = "reused"
STATUS_SKIPPED = "skipped"


def shot(node: str, index: int) -> str:
    """给节点名加镜/段序号后缀（1-based）。见模块文档「序号是时间线可用的前提」。"""
    return f"{node}#{index + 1}"


def append(trace: list | None, node: str, status: str,
           started_at: float | None = None, *,
           elapsed_ms: int | None = None) -> list:
    """追加一条极简轨迹，返回**同一个列表**（支持 `trace = append(trace, ...)`）。

    - `trace` 允许为 `None`（节点里常见 `state.get("trace")`）
    - `started_at` 用 `time.time()` 取（与项目其余计时一致；没给则耗时记 0）
    - 也可以直接给 `elapsed_ms`（调用方已经自己量好了）
    - 超过 `TRACE_MAX` 丢**最老**的条目，保留最新
    """
    if trace is None:
        trace = []
    if elapsed_ms is None:
        elapsed_ms = int((time.time() - started_at) * 1000) if started_at is not None else 0
    trace.append({
        "node": node,
        "status": status,
        "elapsed_ms": max(0, int(elapsed_ms)),
    })
    if len(trace) > TRACE_MAX:
        del trace[:len(trace) - TRACE_MAX]
    return trace
