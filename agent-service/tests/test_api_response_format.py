"""测试 FastAPI 返回体格式与 Java CommonResult 对齐。

验证 Java TaskServiceImpl 用 CommonResult.class 反序列化时 data 不为 null。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state import TaskStatus


@pytest.fixture
def client():
    return TestClient(app)


def test_create_video_task_response_format(client, monkeypatch):
    """POST /v1/tasks/video 返回体必须包含 code/message/data 三层结构。"""
    # 注入 mock gateway 避免触发真实 LangGraph 执行
    from app import graph as graph_mod
    from app.state import TaskStatus
    original_compile = graph_mod.compiled_graph

    class DummyCompiledGraph:
        async def ainvoke(self, state, **kwargs):
            state["status"] = TaskStatus.VIDEO_GENERATING
            return state

    monkeypatch.setattr(graph_mod, "compiled_graph", DummyCompiledGraph())

    resp = client.post("/v1/tasks/video", json={
        "prompt": "测试视频生成",
        "user_id": "test-user"
    })

    assert resp.status_code == 202
    body = resp.json()

    # 必须有 code/message/data 三层（与 Java CommonResult 对齐）
    assert "code" in body, f"缺少 code 字段，响应: {body}"
    assert "message" in body, f"缺少 message 字段，响应: {body}"
    assert "data" in body, f"缺少 data 字段，响应: {body}"

    # code=0 表示成功
    assert body["code"] == 0
    assert body["message"] == "ok"

    # data 是 dict，包含 session_id、status、queue_position
    data = body["data"]
    assert isinstance(data, dict), f"data 应为 dict，实际: {type(data)}"
    assert "session_id" in data, f"data 缺少 session_id，实际 keys: {data.keys()}"
    assert "status" in data, f"data 缺少 status，实际 keys: {data.keys()}"
    # 调度器入队后状态为 queued，并带排队编号
    assert data["status"] == "queued"
    assert data.get("queue_position", 0) >= 1


def test_get_task_response_format(client, monkeypatch):
    """GET /v1/tasks/{session_id} 返回体格式正确。"""
    # 注入 mock session
    from app.main import _sessions
    _sessions["test-session-001"] = {
        "session_id": "test-session-001",
        "user_id": "test-user",
        "raw_prompt": "测试",
        "status": TaskStatus.VIDEO_GENERATING,
        "brief": {"theme": "测试"},
        "script": [],
        "storyboard": [],
        "video_urls": [],
        "trace": [],
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
        "error_message": None,
        "created_at": 0,
        "updated_at": 0,
    }

    resp = client.get("/v1/tasks/test-session-001")
    assert resp.status_code == 200
    body = resp.json()

    assert body["code"] == 0
    assert body["message"] == "ok"
    assert isinstance(body["data"], dict)
    assert body["data"]["session_id"] == "test-session-001"
    assert body["data"]["status"] == "video_generating"


def _base_session(sid: str) -> dict:
    return {
        "session_id": sid,
        "user_id": "test-user",
        "raw_prompt": "测试",
        "status": TaskStatus.COMPLETED,
        "brief": {}, "script": [], "storyboard": [],
        "video_urls": [], "trace": [],
        "fix_round": 0, "max_fix_rounds": 3, "fix_history": [],
        "error_message": None, "created_at": 0, "updated_at": 0,
    }


def test_get_task_exposes_qc_report(client):
    """GET /v1/tasks/{id} 必须返回逐镜 qc_report（A7）。

    这是质检明细**唯一**的出口：画廊卡片上的汇总走 Java 的 error_message
    （notify_final 写进去的一句话），逐镜原因只能从这里取。
    """
    from app.main import _sessions
    state = _base_session("test-qc-001")
    state["qc_report"] = {
        "passed": False,
        "total_shots": 2,
        "failed_shots": [1],
        "shots": [
            {"index": 0, "passed": True, "error": "", "blur_frame_ratio": 0.0},
            {"index": 1, "passed": False, "error": "画面质检未通过（模糊帧比例 90%）",
             "blur_frame_ratio": 0.9},
        ],
        "reason": "1/2 镜未通过质检",
    }
    _sessions["test-qc-001"] = state

    resp = client.get("/v1/tasks/test-qc-001")
    assert resp.status_code == 200
    qc = resp.json()["data"]["qc_report"]

    assert qc["passed"] is False
    assert qc["total_shots"] == 2
    assert qc["failed_shots"] == [1]
    assert qc["shots"][1]["error"]
    # 回归护栏：跳过字段绝不能复活（它是 QC 从未生效的元凶）
    assert "skipped" not in qc
    assert all("skipped" not in s for s in qc["shots"])


def test_get_task_qc_report_is_null_when_not_run(client):
    """QC 未跑的链路（图片任务 / 合成视频）→ qc_report 为 null，而不是缺字段。

    前端据此区分「没质检」与「质检通过」，缺字段会让两者混淆。
    """
    from app.main import _sessions
    _sessions["test-qc-002"] = _base_session("test-qc-002")

    resp = client.get("/v1/tasks/test-qc-002")
    data = resp.json()["data"]

    assert "qc_report" in data
    assert data["qc_report"] is None


def test_scheduler_snapshot_endpoint(client):
    """GET /v1/scheduler 返回执行中/排队中的快照结构。"""
    resp = client.get("/v1/scheduler")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert "running" in data and "queued" in data
    assert "running_count" in data and "queued_count" in data


def test_cancel_queued_session(client, monkeypatch):
    """取消一个排队中的会话：状态变 failed + 从队列移除。"""
    from app.main import _sessions
    _sessions["test-cancel-1"] = {
        "session_id": "test-cancel-1",
        "user_id": "test-user",
        "raw_prompt": "取消测试",
        "status": TaskStatus.QUEUED,
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
        "trace": [],
        "created_at": 0,
        "updated_at": 0,
    }
    from app.scheduler import SessionScheduler
    # 用「未启动」的独立调度器替换，避免全局 worker 抢先消费队列造成竞态
    fresh = SessionScheduler(max_concurrent=1, maxsize=10)
    fresh.submit("test-cancel-1")
    monkeypatch.setattr("app.main.scheduler", fresh)

    resp = client.post("/v1/tasks/test-cancel-1/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["canceled"] is True
    assert _sessions["test-cancel-1"]["status"] == TaskStatus.FAILED

    # 取消未知会话 → 404
    resp2 = client.post("/v1/tasks/test-cancel-9/cancel")
    assert resp2.status_code == 404
    # 清理注入的 session，避免影响其它测试
    _sessions.pop("test-cancel-1", None)