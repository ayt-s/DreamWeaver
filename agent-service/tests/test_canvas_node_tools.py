"""画布结构工具（P1-B）的回归测试：用忠实复刻的 Java stub，不依赖真实服务。

覆盖：新增节点（自动 id / 自动连成片 / 排到最后）、重排顺序、删除节点（连带清边 /
拒绝删成片节点）、连线的重复与自环、以及乐观锁冲突时不写入。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.agent import tools as T

NODES = [
    {"id": "img0", "type": "imageNode", "position": {"x": 120, "y": 60},
     "data": {"imageUrl": "", "prompt": "镜头一", "ratio": "16:9"}},
    {"id": "img1", "type": "imageNode", "position": {"x": 460, "y": 60},
     "data": {"imageUrl": "", "prompt": "镜头二", "ratio": "16:9"}},
    {"id": "compose", "type": "videoNode", "position": {"x": 1300, "y": 200}, "data": {"seconds": 5}},
]
EDGES = [{"id": "e0-ic", "source": "img0", "target": "compose"},
         {"id": "e1-ic", "source": "img1", "target": "compose"}]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, data):
        body = json.dumps({"code": 0, "message": "ok", "data": data}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s = self.server.state
        self._send({"id": 1, "name": "stub", "version": s["version"],
                    "nodesJson": json.dumps({"nodes": s["nodes"]}, ensure_ascii=False),
                    "edgesJson": json.dumps(s["edges"], ensure_ascii=False)})

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or "{}")
        s = self.server.state
        if body.get("version") is not None and body["version"] != s["version"]:
            return self._send({"canvas": None, "conflict": True, "serverVersion": s["version"],
                               "serverNodesJson": json.dumps({"nodes": s["nodes"]}, ensure_ascii=False),
                               "serverEdgesJson": json.dumps(s["edges"], ensure_ascii=False)})
        s["nodes"], _ = T._parse_nodes_json(body["nodesJson"])
        s["edges"] = json.loads(body.get("edgesJson") or "[]")
        s["version"] += 1
        return self._send({"canvas": {"id": 1, "version": s["version"]}, "conflict": False})


@pytest.fixture()
def stub(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.state = {"nodes": [dict(x) for x in NODES], "edges": [dict(e) for e in EDGES], "version": 1}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(T, "JAVA_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield srv.state
    srv.shutdown()


def test_add_image_node_appends_id_connects_and_sorts_last(stub):
    r = T.add_image_node(1, "  新镜头  ")
    assert r["saved"] is True and r["node_id"] == "img2" and r["connected_to"] == "compose"
    ids = [n["id"] for n in stub["nodes"]]
    assert ids == ["img0", "img1", "compose", "img2"]
    new = stub["nodes"][-1]
    assert new["data"]["prompt"] == "新镜头", "提示词应去空白后落库"
    assert new["position"]["x"] > 460, "默认排在最右（= 顺序的最后）"
    last_edge = stub["edges"][-1]
    assert last_edge["source"] == "img2" and last_edge["target"] == "compose"
    assert r["version"] == 2


def test_add_image_node_after_given_node_places_next_to_it(stub):
    r = T.add_image_node(1, "插在 img0 之后", after_node_id="img0")
    assert r["saved"] and r["position"]["x"] == 460, r  # 120 + 340
    assert "插在 img0 之后" in r["message"]


def test_add_image_node_rejects_blank_prompt_and_missing_anchor(stub):
    assert "不能为空" in T.add_image_node(1, "   ")["error"]
    assert "不存在" in T.add_image_node(1, "x", after_node_id="nope")["error"]


def test_reorder_shots_rewrites_x_keeping_y(stub):
    r = T.reorder_shots(1, ["img1", "img0"])
    assert r["saved"] and r["order"] == ["img1", "img0"]
    by_id = {n["id"]: n for n in stub["nodes"]}
    assert by_id["img1"]["position"]["x"] < by_id["img0"]["position"]["x"]
    assert by_id["img1"]["position"]["y"] == 60, "y 不该被改动"


def test_reorder_shots_rejects_unknown_or_non_image(stub):
    assert "不存在" in T.reorder_shots(1, ["img0", "nope"])["error"]
    assert "不是图片节点" in T.reorder_shots(1, ["compose"])["error"]
    assert "不能为空" in T.reorder_shots(1, [])["error"]


def test_delete_node_removes_its_edges(stub):
    r = T.delete_node(1, "img1")
    assert r["saved"] and r["removed_edges"] == 1 and r["remaining_nodes"] == 2
    assert "img1" not in [n["id"] for n in stub["nodes"]]
    assert all("img1" not in (e["source"], e["target"]) for e in stub["edges"])


def test_delete_compose_is_refused_and_nothing_written(stub):
    before = json.dumps(stub["nodes"], sort_keys=True)
    r = T.delete_node(1, "compose")
    assert "不能删除成片节点" in r["error"]
    assert json.dumps(stub["nodes"], sort_keys=True) == before, "拒绝时不能改动画布"
    assert stub["version"] == 1


def test_connect_nodes_dedupes_and_rejects_self_loop(stub):
    assert T.connect_nodes(1, "img0", "compose")["saved"] is False  # 已存在
    r = T.connect_nodes(1, "img2", "compose") if "img2" in [n["id"] for n in stub["nodes"]] \
        else T.connect_nodes(1, "img0", "img1")
    assert r["saved"] is True, r
    assert "不能把节点连到自己" in T.connect_nodes(1, "img0", "img0")["error"]
    assert "不存在" in T.connect_nodes(1, "img0", "nope")["error"]


def test_conflict_does_not_write_and_tells_llm_what_to_do(stub, monkeypatch):
    # 让工具拿到过期版本：读完画布后、写之前偷偷把服务端版本推进一格
    real_get = T._get_canvas

    def stale(canvas_id):
        snap = real_get(canvas_id)
        snap["version"] = (snap["version"] or 0) + 99
        return snap

    monkeypatch.setattr(T, "_get_canvas", stale)
    before = json.dumps(stub["nodes"], sort_keys=True)
    r = T.add_image_node(1, "冲突测试")
    assert r["conflict"] is True and r["saved"] is False
    assert json.dumps(stub["nodes"], sort_keys=True) == before, "冲突时严禁写入"
    assert "inspect_canvas" in r["message"], "要告诉 LLM 下一步怎么做"


def test_compose_sink_falls_back_to_most_common_target(monkeypatch):
    """没有 compose 节点时，取图片节点出边里出现最多的目标。"""
    def fake(canvas_id):
        return {"nodes": [dict(x) for x in NODES[:2]] + [
                    {"id": "sink", "type": "videoNode", "position": {"x": 900, "y": 0}, "data": {}}],
                "edges": [{"id": "a", "source": "img0", "target": "sink"},
                          {"id": "b", "source": "img1", "target": "sink"}],
                "wrapper": None, "version": 1, "project": {"id": canvas_id}}

    monkeypatch.setattr(T, "_get_canvas", fake)
    assert T._compose_id(fake(1)["nodes"], fake(1)["edges"]) == "sink"
