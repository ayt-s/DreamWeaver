"""会话中止标志（进程内旁路信号）。

用途：Agent 自动恢复一个会话后，若用户在 Java 侧把该任务「全量重生」或删除了，
原会话已经无人认领——继续跑只会白烧 agnes 额度（回调会因 session_id 不匹配被
丢弃）。心跳每 60 秒会问一次 Java「这个会话还在吗」，得到明确否定答复（
任务查不到 / 已是终态）就置位这里；各节点在**花钱的提交点**前检查，命中即跳过
后续提交。

为什么不放 state：LangGraph 每个节点拿到的是各自合并出的新 state，`_run_session`
里改输入 dict 不会传播到节点，所以中止信号需要一个进程内旁路通道。

只在「明确判定」(Java 明确回复 tracked=false) 时置位——Java 不可达/响应异常一律
不置位，避免把仍在正常跑的会话误中止成残缺产物。
"""
import logging

logger = logging.getLogger(__name__)

_aborted: set[str] = set()


def mark(session_id: str) -> None:
    """标记会话已中止（幂等）。"""
    if session_id:
        _aborted.add(session_id)


def is_aborted(session_id: str) -> bool:
    """该会话是否已被判定为「Java 侧无人认领」。"""
    return bool(session_id) and session_id in _aborted


def clear(session_id: str) -> None:
    """会话结束时清理标志，避免长期驻留（进程内集合会持续增长）。"""
    _aborted.discard(session_id)
