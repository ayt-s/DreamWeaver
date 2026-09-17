"""`friendly_error_message` 的文案口径 —— 失败原因必须指向**真正的下一跳**。

## 来源（2026-09-17 真实任务实测）

任务 53 因 agnes 连接失败而失败（agent 日志：`httpx.ConnectError`
during `requirement_parser`），Java 侧存下并展示给用户的却是

    Agent 服务暂不可用，请稍后重试

—— 而 agent 活得好好的（端口在听、探活 200）。**用户按这句话会去重启 agent，
方向完全错了。**

根因：`_RAW_ERROR_CN` 把网络层异常统一映射成「Agent 服务…」。但这个模块
**跑在 agent 进程内**，这里的连接失败几乎只可能是 agent 自己出网（调 agnes /
回调 Java）。Java 侧 `TaskServiceImpl` 里的「Agent 服务暂不可用」是对的
（那是 Java 连不上 agent），两处语义不同 —— 之前是把 Java 的口径抄了过来。

所以这里锁两件事：
1. 网络/超时类异常必须说清是**平台/网络**，不能指向 agent 自己；
2. 表里**任何**文案都不许再出现「Agent 服务」（结构性护栏，防改回去）。
"""

import asyncio
import socket

import httpx
import pytest

from app.errors import AppError, _RAW_ERROR_CN, friendly_error_message


# --------------------------------------------------------------- 网络层归属


def test_connect_error_blames_the_platform_not_the_agent():
    """★ 回归护栏：这正是任务 53 那条错误文案。"""
    msg = friendly_error_message(httpx.ConnectError("All connection attempts failed"))

    assert "Agent 服务" not in msg, f"指向了错误的组件（用户会去重启 agent）: {msg}"
    assert "连接生成平台失败" in msg
    assert "网络" in msg, "要给出下一步（检查网络）"


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("All connection attempts failed"),
    httpx.ConnectError("Connection refused"),
    httpx.ConnectError("Connection reset by peer"),
    socket.gaierror("Name or service not known"),
])
def test_network_errors_never_blame_the_agent(exc):
    msg = friendly_error_message(exc)

    assert "Agent 服务" not in msg, f"{type(exc).__name__} 的文案指错组件: {msg}"
    assert msg.strip(), "文案不能为空"


def test_dns_error_is_specific():
    msg = friendly_error_message(socket.gaierror("Name or service not known"))

    assert "域名解析失败" in msg
    assert "DNS" in msg


def test_timeout_mentions_platform_queueing():
    """超时要说「平台排队/限流时常见」—— 否则用户以为平台挂了。

    agnes 免费额度只有 RPM 限制，排队时响应变慢是常态（后端 gateway 侧
    的诊断口径与这条一致，见 f84232c）。
    """
    for exc in (httpx.ReadTimeout(""), asyncio.TimeoutError()):
        msg = friendly_error_message(exc)
        assert "超时" in msg
        assert "排队" in msg, f"缺了「为什么会超时」的线索: {msg}"


# ----------------------------------------------------------- 结构性护栏（全表）


def test_no_message_blames_the_agent_service():
    """★ 整张表都不许出现「Agent 服务」——这是本模块最容易抄错的一句话。

    结构性检查（而不是只测一两个用例）：以后有人新增映射时，只要把 Java 侧那句
    随手抄进来就会红。
    """
    offenders = [(key, text) for key, text in _RAW_ERROR_CN if "Agent 服务" in text]

    assert not offenders, f"这些映射又把失败指向了 agent 自己: {offenders}"


def test_every_message_is_actionable():
    """每条文案都要有「怎么办」的语气词（请/检查/稍后/重试/联系），不能只是陈述。"""
    vague = [(key, text) for key, text in _RAW_ERROR_CN
             if not any(w in text for w in ("请", "重试", "检查", "联系"))]

    assert not vague, f"这些文案没说下一步怎么办: {vague}"


# ------------------------------------------------------------------ 其它分支


def test_app_error_message_passes_through():
    """业务异常已经中文化 → 原样返回，不二次翻译。"""
    assert friendly_error_message(AppError("会话不存在", status_code=404)) == "会话不存在"


@pytest.mark.parametrize("raw", [
    "视频接口拒绝 (400): 参考图必须为公网 URL",
    "多镜拼接失败，分段仍可下载",
])
def test_already_localized_text_is_not_retranslated(raw):
    """gateway 侧已经产出的中文文案原样保留（否则「公网 URL」这类关键提示会被吃掉）。"""
    assert friendly_error_message(RuntimeError(raw)) == raw


def test_unknown_exception_falls_back_to_non_empty():
    """未知异常也不能给空串 —— 用户看到空白会比看到英文更慌。"""
    msg = friendly_error_message(ValueError("something weird"))

    assert msg.strip()
    assert isinstance(msg, str)
