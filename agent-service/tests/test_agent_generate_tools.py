"""画布助手的**生成类与写画布工具**回归测试（2026-09-24 补）。

此前只有画布**结构**工具（add/delete/connect/reorder）有测试；`generate_images` /
`collect_images` / `submit_task` / `edit_prompt` / `concat_task` 一条都没有 ——
而它们正是「会真的提交任务、会写用户画布」的那部分。

⚠️ 成本不是这里的理由（agnes 当前在本项目用的都是免费额度）；本文件锁的是**功能正确性**：

1. `dry_run` 闸门与 `MAX_IMAGES_PER_CALL` 硬上限 —— 「先报计划再执行」不能被绕过；
2. 提交载荷逐字段正确（`genType`/`directImage`/`imageCount`/`source`，且**不带 userId**
   —— Java 侧 `@Pattern` 只接受数字，自造字符串会被 400）；
3. 回填只填「还没有图」的节点（用户自己挑过的图不能被覆盖）；
4. 提示词匹配走归一化（用户改个标点就匹配不上 = 图在库里却不回填）；
5. 超时/未完成的任务留在 `pending_task_ids` 而不是被当成失败。

打的是 Java 8080 的忠实 stub（真实 httpx 走一遍工具层的 HTTP 路径），不打 agnes。
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.agent import tools as T


def _nodes():
    return [
        {"id": "img0", "type": "imageNode", "position": {"x": 0, "y": 0},
         "data": {"prompt": "[角色锚] 一只猫坐在窗台", "imageUrl": "", "ratio": "16:9"}},
        {"id": "img1", "type": "imageNode", "position": {"x": 300, "y": 0},
         "data": {"prompt": "已经挑好图的镜头", "imageUrl": "http://old/keep.jpg"}},
        {"id": "img2", "type": "imageNode", "position": {"x": 600, "y": 0},
         "data": {"prompt": "第二镜：少年走向山坡", "imageUrl": ""}},
        {"id": "img3", "type": "imageNode", "position": {"x": 900, "y": 0},
         "data": {"prompt": "第三镜：远山与飞鸟", "imageUrl": ""}},
        {"id": "t0", "type": "textNode", "position": {"x": 0, "y": 300},
         "data": {"content": "文本节点正文"}},
        {"id": "compose", "type": "videoNode", "position": {"x": 1200, "y": 0},
         "data": {"seconds": 5}},
    ]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _json(self, data, code=0, status=200):
        body = json.dumps({"code": code, "message": "ok", "data": data},
                          ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- routes ----
    def do_GET(self):
        s = self.server.state
        path = self.path
        if path.startswith("/api/canvas/"):
            c = s["canvas"]
            return self._json({"id": c["id"], "name": c["name"], "version": c["version"],
                               "nodesJson": json.dumps({"nodes": c["nodes"]}, ensure_ascii=False),
                               "edgesJson": json.dumps(c["edges"], ensure_ascii=False)})
        if path.startswith("/api/tasks/"):
            tid = int(path.rstrip("/").rsplit("/", 1)[-1])
            return self._json(s["tasks"].get(tid))
        if path.startswith("/api/tasks"):
            return self._json({"list": list(s["tasks"].values())})
        return self._json(None)

    def do_PUT(self):
        s = self.server.state
        body = self._body()
        if body.get("version") is not None and body["version"] != s["canvas"]["version"]:
            return self._json({"canvas": None, "conflict": True,
                               "serverVersion": s["canvas"]["version"],
                               "serverNodesJson": json.dumps(
                                   {"nodes": s["canvas"]["nodes"]}, ensure_ascii=False),
                               "serverEdgesJson": json.dumps(s["canvas"]["edges"],
                                                             ensure_ascii=False)})
        nodes, _ = T._parse_nodes_json(body["nodesJson"])
        s["canvas"]["nodes"] = nodes
        s["canvas"]["edges"] = json.loads(body.get("edgesJson") or "[]")
        s["canvas"]["version"] += 1
        s["saves"].append(body)
        return self._json({"canvas": {"id": s["canvas"]["id"],
                                      "version": s["canvas"]["version"]},
                           "conflict": False})

    def do_POST(self):
        s = self.server.state
        path = self.path
        if path == "/api/tasks/video":
            payload = self._body()
            s["submitted"].append(payload)
            tid = s["next_id"]
            s["next_id"] += 1
            prompt = payload.get("prompt", "")
            if s.get("strip_brackets"):
                # 模拟「用户改过一个标点/方括号被剥掉」的任务提示词
                prompt = re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", " ", prompt)).strip()
            s["tasks"][tid] = {
                "id": tid,
                "status": s.get("task_status", "completed"),
                "prompt": prompt,
                "imageUrls": json.dumps(s.get("image_urls") or ["http://img/1.jpg",
                                                               "http://img/2.jpg"]),
                "resultJson": json.dumps(s.get("video_urls") or []),
                "errorMessage": s.get("task_error"),
            }
            return self._json({"id": tid, "status": "queued"})
        if path.endswith("/concat"):
            tid = int(path.rstrip("/").rsplit("/", 2)[-2])
            t = s["tasks"].get(tid) or {"id": tid, "status": "completed"}
            return self._json(t)
        return self._json(None)


@pytest.fixture()
def stub(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.state = {
        "canvas": {"id": 1, "name": "stub", "version": 1, "nodes": _nodes(), "edges": []},
        "tasks": {}, "submitted": [], "saves": [], "next_id": 101,
    }
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(T, "JAVA_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield srv.state
    srv.shutdown()


def _canvas_nodes(stub):
    return {n["id"]: n for n in stub["canvas"]["nodes"]}


# === 计划闸门 ===============================================================

def test_dry_run是默认值且一个任务都不提交(stub):
    plan = T.generate_images(1, count=3)
    assert plan["dry_run"] is True
    assert stub["submitted"] == []                     # 关键：默认不动作
    assert [t["node_id"] for t in plan["targets"]] == ["img0", "img2", "img3"]
    assert plan["estimated_images"] == 9
    assert "按张计费" in plan["message"]


def test_超过单次上限即使用户已同意也拒绝(stub):
    # 3 个待生成节点 × 5 张 = 15 > MAX_IMAGES_PER_CALL(12)
    res = T.generate_images(1, count=5, dry_run=False)
    assert res["refused"] is True
    assert stub["submitted"] == []                     # 拒绝发生在提交之前
    assert "超过单次上限" in res["message"]


def test_已有图的节点不在计划里(stub):
    plan = T.generate_images(1, count=1)
    assert "img1" not in [t["node_id"] for t in plan["targets"]]


def test_指定node_ids时只处理这些节点(stub):
    plan = T.generate_images(1, node_ids=["img2"], count=1)
    assert [t["node_id"] for t in plan["targets"]] == ["img2"]


def test_没有待生成节点时明确说清而不是空动作(stub):
    stub["canvas"]["nodes"] = [n for n in stub["canvas"]["nodes"] if n["id"] == "t0"]
    res = T.generate_images(1, count=3, dry_run=False)
    assert "没有待生成的图片节点" in res["message"]
    assert stub["submitted"] == []


# === 真执行：载荷与回填 =====================================================

def test_执行时每个节点一个任务且载荷逐字段正确(stub):
    res = T.generate_images(1, count=2, dry_run=False, wait_seconds=0)

    assert len(stub["submitted"]) == 3
    for payload in stub["submitted"]:
        assert payload["genType"] == "text_image"
        assert payload["directImage"] is True
        assert payload["imageCount"] == 2
        assert payload["source"] == "canvas_asset"
        # Java 侧 CreateTaskRequest.userId 只接受数字；前端从来不带 ⇒ 助手也不能自造
        assert "userId" not in payload

    assert set(res["filled_node_ids"]) == {"img0", "img2", "img3"}
    assert res["saved"] is True


def test_回填写入imageUrl与候选且不碰已有图的节点(stub):
    T.generate_images(1, count=2, dry_run=False, wait_seconds=0)
    nodes = _canvas_nodes(stub)

    assert nodes["img0"]["data"]["imageUrl"] == "http://img/1.jpg"
    assert nodes["img0"]["data"]["candidates"] == ["http://img/1.jpg", "http://img/2.jpg"]
    assert nodes["img2"]["data"]["imageUrl"] == "http://img/1.jpg"
    # 用户自己挑过的图不能被覆盖
    assert nodes["img1"]["data"]["imageUrl"] == "http://old/keep.jpg"
    # 文本节点无关
    assert nodes["t0"]["data"]["content"] == "文本节点正文"


def test_提示词归一化后仍能匹配回填(stub):
    """用户改过标点/方括号被剥掉时，图其实还在库里 —— 严格匹配会让它永远回填不上。"""
    stub["strip_brackets"] = True
    res = T.generate_images(1, count=1, dry_run=False, wait_seconds=0)
    assert "img0" in res["filled_node_ids"]


def test_仍未完成的任务留在pending而不是被当成失败(stub):
    stub["task_status"] = "running"
    res = T.generate_images(1, count=1, dry_run=False, wait_seconds=0)
    assert len(res["pending_task_ids"]) == 3
    assert res["filled_node_ids"] == []
    assert res["failed"] == []                         # 在跑 ≠ 失败
    assert "仍在生成" in res["message"]


def test_失败任务带原因进failed(stub):
    stub["task_status"] = "failed"
    stub["task_error"] = "图片队列繁忙"
    res = T.generate_images(1, count=1, dry_run=False, wait_seconds=0)
    assert len(res["failed"]) == 3
    assert res["failed"][0]["error"] == "图片队列繁忙"


def test_乐观锁冲突时不写入并提示可重试(stub):
    """回填时画布被别人改过 ⇒ 不覆盖对方的改动，且图不用重新生成。"""
    stub["canvas"]["version"] = 1
    orig_get_canvas = T._get_canvas

    def _race(canvas_id):
        snap = orig_get_canvas(canvas_id)
        stub["canvas"]["version"] += 1      # 模拟「读完之后用户又改了」
        return snap

    T._get_canvas = _race
    try:
        res = T.generate_images(1, count=1, dry_run=False, wait_seconds=0)
    finally:
        T._get_canvas = orig_get_canvas

    assert res["conflict"] is True
    assert res["saved"] is False
    assert "不必重新生成" in res["hint"]


# === 续收 / 拼接 / 写画布 ===================================================

def test_续收把pending里的已完成项回填(stub):
    T.generate_images(1, count=1, dry_run=False, wait_seconds=0)
    tids = list(stub["tasks"].keys())
    # 图已重生成好但之前没来得及收 —— 模拟「用户隔一会儿说图好了没」
    stub["canvas"]["nodes"] = [n for n in _nodes()]    # 清掉刚才的回填
    res = T.collect_images(1, tids, wait_seconds=0)
    assert set(res["filled_node_ids"]) == {"img0", "img2", "img3"}


def test_续收空id直接报错(stub):
    assert "error" in T.collect_images(1, [])


def test_拼接成片是幂等的(stub):
    tid = 200
    stub["tasks"][tid] = {"id": tid, "status": "completed",
                          "resultJson": json.dumps(["http://v/final.mp4"])}
    res = T.concat_task(tid)
    assert res["task_id"] == tid
    assert res["urls"] == ["http://v/final.mp4"]
    assert "幂等" in res["message"]


def test_编辑提示词按节点类型选字段(stub):
    text_res = T.edit_prompt(1, "t0", "改写后的正文")
    assert text_res["field"] == "content"
    img_res = T.edit_prompt(1, "img2", "改写后的镜头")
    assert img_res["field"] == "prompt"

    nodes = _canvas_nodes(stub)
    assert nodes["t0"]["data"]["content"] == "改写后的正文"
    assert nodes["img2"]["data"]["prompt"] == "改写后的镜头"


def test_编辑空提示词与不存在的节点被挡(stub):
    assert "error" in T.edit_prompt(1, "img2", "   ")
    assert "error" in T.edit_prompt(1, "nope", "x")


def test_读单个节点返回它的data(stub):
    n = T.read_node(1, "img0")
    assert n["id"] == "img0"
    assert n["data"]["prompt"].startswith("[角色锚]")
    assert "error" in T.read_node(1, "nope")


# === 任务查询 ===============================================================

def test_提交任务的载荷会丢掉None且改成Java驼峰(stub):
    res = T.submit_task(gen_type="image_video", prompt="一镜", video_model="agnes-video-2.5",
                        slide_seconds=3)
    assert res["task_id"] == 101
    payload = stub["submitted"][-1]
    assert payload == {"genType": "image_video", "prompt": "一镜",
                       "videoModel": "agnes-video-2.5", "slideSeconds": 3}
    assert "imageCount" not in payload and "source" not in payload


def test_查任务会标注prompt被截断(stub):
    long_prompt = "长" * 300
    T.submit_task(gen_type="text_image", prompt=long_prompt)
    tid = stub["submitted"] and list(stub["tasks"])[-1]
    t = T.get_task(tid)
    assert t["prompt_truncated"] is True
    assert len(t["prompt"]) < len(long_prompt)
    assert t["image_urls"][:1] == ["http://img/1.jpg"]


def test_列任务返回list结构(stub):
    T.submit_task(gen_type="text_image", prompt="x")
    out = T.list_tasks()
    assert isinstance(out["list"], list) and len(out["list"]) == 1
