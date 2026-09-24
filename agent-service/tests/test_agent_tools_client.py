"""工具层 HTTP 客户端（2026-09-23 改）：连接复用 + 不吃环境代理。

两件事都必须**实测**，不能只看代码：
1. 复用连接：服务端记录每次请求的来源端口，两次工具调用应落在同一端口
   （`httpx.get/post` 顶层函数每次新建 Client，端口必然不同）；
2. `trust_env=False`：进程里塞一个必然连不上的 HTTP_PROXY，工具调用仍要成功
   —— 若 trust_env 生效，请求会走那个死代理并失败。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.agent import tools as T


class _KeepAliveHandler(BaseHTTPRequestHandler):
    # 默认是 HTTP/1.0：每个请求后关连接，那样**测不出复用**（会假失败）
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.server.peer_ports.append(self.client_address[1])
        body = json.dumps({"code": 0, "message": "ok", "data": {"list": []}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def stub(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    srv.peer_ports = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(T, "JAVA_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield srv
    srv.shutdown()


def test_连续工具调用复用同一条连接(stub):
    T.list_tasks()
    T.list_tasks()
    assert len(stub.peer_ports) == 2
    assert len(set(stub.peer_ports)) == 1, (
        f"两次调用应复用同一 TCP 连接（keep-alive），实际端口 {stub.peer_ports}"
    )


def test_环境代理不介入工具调用(stub, monkeypatch):
    """往进程里塞一个必然连不上的代理：工具调用仍要成功（`trust_env=False` 的行为证据）。

    ⚠️ 两条别误判的相邻事实：

    1. 只断言 `trust_env is False` 是**配置级**证据；注入 env 后真打一次是**行为级**证据，
       强度不同，这里保留后者。
    2. 跑全量时输出里那段 `Failed to multipart ingest runs … 127.0.0.1:9` **不是本用例造成的**
       —— 它来自既有的 `tests/test_observability_flag_divergence.py`（那里把
       `LANGSMITH_ENDPOINT` 指到 `127.0.0.1:9` 来测开关分歧，SDK 后台上报线程照打）。
       我第一次误判成自己引入的，还写了「delenv 就能压住」的错误结论 —— `delenv` 只影响
       后续新建的客户端，压不住已经启动的后台线程。
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    assert isinstance(T._CLIENT, httpx.Client)
    assert T._CLIENT.trust_env is False
    assert T._CLIENT._mounts == {}          # 没挂任何代理 ⇒ 所有请求直连
    # 真打一次：能拿到 stub 的响应 ⇒ 代理没被使用
    assert T.list_tasks() == {"list": []}


def test_客户端是模块级单例(stub):
    first = T._CLIENT
    T.list_tasks()
    assert T._CLIENT is first
