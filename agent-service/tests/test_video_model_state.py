"""#22·#32：画布选的「视频模型」必须真的进会话 state（2026-09-19 修）。

## 缺陷
画布底部「视频模型」下拉（Agnes Video 2.5 / HD）的值提交时是发出去的
（`CreateVideoTaskRequest.video_model` :98、Java `TaskServiceImpl:438 body.put("video_model", ...)`），
但运行态 state 字面量**从没写这个键** ⇒ `nodes/video.py:112` 读到的永远是 None
⇒ 网关回落到 `settings.video_model_fast`（Flash）：**选 HD 完全无效**，档位/画幅规则都不同，
而且没有任何报错或提示。

## 本用例证明
提交接口收到 video_model 后，会话 state 里确实带着它；脏值一律回落 None（= 网关默认）。
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from app import main as m

    # 不让调度器真的执行会话（只用提交接口，不跑图）
    monkeypatch.setattr(m.scheduler, "submit", lambda sid: 0)
    return TestClient(m.app)


def _submit(client, model):
    body = {
        "user_id": "tester",
        "prompt": "画布任务",
        "gen_type": "image_video",
        # ⚠️ segments 在 HTTP 契约里是 **JSON 字符串**（agent 侧 _parse_segments 自己解析），
        #    传数组会被 pydantic 以 422 拒掉
        "segments": json.dumps([{"index": 0, "image_url": "http://mock/a.png", "prompt": "p",
                                 "seconds": 5, "aspect_ratio": "16:9"}]),
    }
    if model is not None:
        body["video_model"] = model
    r = client.post("/v1/tasks/video", json=body)
    assert r.status_code in (200, 202), r.text   # 提交接口是 202 Accepted（队列已受理）
    return r.json()["data"]["session_id"]


def test_hd_video_model_reaches_state(client, monkeypatch):
    from app import main as m
    from app.config import settings

    sid = _submit(client, settings.video_model_hd)
    state = m._sessions.get(sid) or {}
    assert state.get("video_model") == settings.video_model_hd, (
        "选 HD 却没进 state ⇒ 网关会回落 Flash，画布上的模型下拉形同虚设")


def test_unknown_video_model_falls_back_to_default(client):
    from app import main as m

    sid = _submit(client, "totally-bogus-model")
    state = m._sessions.get(sid) or {}
    assert state.get("video_model") is None, "脏值必须回落 None（网关默认 Flash），不能透传给上游"


def test_absent_video_model_is_none(client):
    from app import main as m

    sid = _submit(client, None)
    assert (m._sessions.get(sid) or {}).get("video_model") is None
