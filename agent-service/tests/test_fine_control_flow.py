"""可灵式精细控制全链路集成测试。

覆盖：请求字段 → state → storyboard 提示词组装（风格/负面词/运镜/元素绑定）。
用 mock gateway 避免真实 LLM/agnes 调用。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.nodes import storyboard as sb_mod


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_graph(monkeypatch):
    """替换 compiled_graph，避免真跑 LangGraph。"""
    from app import graph as graph_mod
    from app.state import TaskStatus

    class DummyCompiledGraph:
        async def ainvoke(self, state, **kwargs):
            state["status"] = TaskStatus.QUEUED
            return state

    monkeypatch.setattr(graph_mod, "compiled_graph", DummyCompiledGraph())


def test_create_task_carries_fine_control_fields(client, mock_graph):
    """新字段必须进入 session state（Java 透传 → agent state 全程不丢）。"""
    resp = client.post("/v1/tasks/video", json={
        "prompt": "荒野雪原上的骑手",
        "user_id": "test-user",
        "style_prompt": "3D写实国漫风，虚幻5",
        "negative_prompt": "手指畸形，穿模",
        "total_seconds": 30,
        "shot_count": 4,
        "reference_bindings": '[{"name":"我","imageIndex":2}]',
    })
    assert resp.status_code == 202
    session_id = resp.json()["data"]["session_id"]

    from app import main as main_mod
    state = main_mod._sessions[session_id]
    assert state["style_prompt"] == "3D写实国漫风，虚幻5"
    assert state["negative_prompt"] == "手指畸形，穿模"
    assert state["total_seconds"] == 30
    assert state["shot_count"] == 4
    assert state["reference_bindings"] == [{"name": "我", "imageIndex": 2}]


def test_create_task_omits_fine_control_when_absent(client, mock_graph):
    """不传时字段为空/None，不产生脏数据。"""
    resp = client.post("/v1/tasks/video", json={"prompt": "普通任务"})
    session_id = resp.json()["data"]["session_id"]
    from app import main as main_mod
    state = main_mod._sessions[session_id]
    assert state["style_prompt"] == ""
    assert state["negative_prompt"] == ""
    assert state["total_seconds"] is None
    assert state["shot_count"] is None
    assert state["reference_bindings"] == []


@pytest.mark.asyncio
async def test_standard_storyboard_injects_style_negative_and_binding(monkeypatch):
    """标准模式：风格/负面词进中文描述参与翻译；绑定句以 <Picture N> 注入英文提示词。"""
    captured: list[str] = []

    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        captured.append(prompt)
        return "A rider on a snowy plain, cinematic lighting."

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)

    state = {
        "session_id": "s1",
        "script": [{"shot_id": 0, "visual": "骑手在雪原", "camera": "远景", "style_note": "冷色调",
                    "duration": 6}],
        "style_prompt": "3D写实国漫风",
        "negative_prompt": "手指畸形",
        "reference_bindings": [{"name": "我", "imageIndex": 2}],
    }
    out = await sb_mod.storyboarder_node(state)
    shot = out["storyboard"][0]

    # 风格/负面词进入翻译输入
    assert "画面风格：3D写实国漫风" in captured[0]
    assert "避免出现：手指畸形" in captured[0]
    # 绑定句注入英文提示词（<Picture N> 1-indexed）
    assert '"我" refers to <Picture 2>' in shot["prompt_en"]
    assert "Keep the appearance of 我" in shot["prompt_en"]
    # 精细控制参数随段落库，供段重生复用
    assert shot["style_prompt"] == "3D写实国漫风"
    assert shot["negative_prompt"] == "手指畸形"


@pytest.mark.asyncio
async def test_canvas_storyboard_injects_camera_phrase(monkeypatch):
    """画布模式：结构化运镜翻译为确定性英文片段，追加到提示词尾部。"""
    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        return "A slow camera move over the reference image."

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)

    state = {
        "session_id": "s2",
        "segments": [{
            "image_url": "https://example.com/a.png",
            "prompt": "雪原上的骑手",
            "seconds": 5,
            "camera_spec": {"shot_size": "远景", "angle": "俯拍", "movement": "跟拍"},
        }],
        "style_prompt": "",
        "negative_prompt": "",
        "reference_bindings": [],
    }
    out = await sb_mod.canvas_storyboarder_node(state)
    shot = out["storyboard"][0]
    assert shot["prompt_en"].endswith("wide shot, high-angle shot, follow shot")
    assert shot["camera_spec"] == {"shot_size": "远景", "angle": "俯拍", "movement": "跟拍"}


@pytest.mark.asyncio
async def test_canvas_storyboard_camera_ignored_when_empty(monkeypatch):
    """无运镜时不追加任何镜头语言，且不塞空对象。"""
    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        return "Plain prompt."

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)

    state = {
        "session_id": "s3",
        "segments": [{"image_url": "https://example.com/a.png", "prompt": "画面", "seconds": 5}],
    }
    out = await sb_mod.canvas_storyboarder_node(state)
    shot = out["storyboard"][0]
    assert shot["prompt_en"] == "Plain prompt."
    assert shot["camera_spec"] == {}


def test_create_task_carries_shot_language(client, mock_graph):
    """③-1 全局运镜倾向：JSON 对象字符串 → state 清洗后的 dict。"""
    resp = client.post("/v1/tasks/video", json={
        "prompt": "雪原骑手",
        "shot_language": '{"shot_size":"远景","angle":"俯拍","movement":"跟拍"}',
    })
    assert resp.status_code == 202
    session_id = resp.json()["data"]["session_id"]
    from app import main as main_mod
    state = main_mod._sessions[session_id]
    assert state["shot_language"] == {"shot_size": "远景", "angle": "俯拍", "movement": "跟拍"}


def test_create_task_shot_language_whitelist_filters_dirty_values(client, mock_graph):
    """白名单外的脏值必须被丢弃，防止注入进提示词。"""
    resp = client.post("/v1/tasks/video", json={
        "prompt": "雪原骑手",
        "shot_language": '{"shot_size":"超超远景","movement":"环绕","evil":"<script>"}',
    })
    session_id = resp.json()["data"]["session_id"]
    from app import main as main_mod
    state = main_mod._sessions[session_id]
    assert state["shot_language"] == {"movement": "环绕"}


def test_create_task_shot_language_defaults_empty(client, mock_graph):
    """不传时为 {}（非 None），storyboarder 无需额外判空。"""
    resp = client.post("/v1/tasks/video", json={"prompt": "普通任务"})
    session_id = resp.json()["data"]["session_id"]
    from app import main as main_mod
    state = main_mod._sessions[session_id]
    assert state["shot_language"] == {}


@pytest.mark.asyncio
async def test_canvas_storyboard_injects_reference_bindings(monkeypatch):
    """④ 画布模式：元素绑定注入 <Picture N>（**仅在 reference 模式**；锁定首帧时不注入，见 #25）。"""
    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        return "The rider gallops across the snowfield."

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)

    def _canvas_state(**over):
        return {
            "session_id": "s6",
            "segments": [{
                "image_url": "https://example.com/seg1.png",
                "reference_images": [
                    "https://example.com/seg1.png",
                    "https://example.com/char.png",
                    "https://example.com/bike.png",
                ],
                "prompt": "骑手冲过雪原",
                "seconds": 5,
            }],
            "reference_bindings": [
                {"name": "我", "imageIndex": 2},
                {"name": "破旧摩托车", "imageIndex": 3},
            ],
            **over,
        }

    # ★ 2026-09-19 更新（#25）：本用例原来断言「绑定一定被注入」—— 而它的 fixture 恰好是
    #   **首帧锁定=开（默认）+ 本段有首帧图** 的那个状态。官方约束 keyframe 与 reference 互斥
    #   （keyframe 不许带 images）⇒ 参考图会被网关丢弃，提示词里再留着 `Picture 2 is "我"`
    #   就是**指向不存在图片**的指令：模型可能理解成「画面里要有这些元素」而画出不该有的对象，
    #   用户也会觉得「绑定明明配了却没效果」查不出原因。所以两个分支都要锁住：
    out = await sb_mod.canvas_storyboarder_node(_canvas_state())
    shot = out["storyboard"][0]
    assert shot["mode"] == "keyframe", "默认锁定首帧"
    assert "<Picture" not in shot["prompt_en"], shot["prompt_en"]
    assert "Keep the appearance of" not in shot["prompt_en"]

    # 关掉首帧锁定 → 回到 reference 模式（参考图真的会发出去），这时绑定才有意义
    out2 = await sb_mod.canvas_storyboarder_node(_canvas_state(lock_first_frame=False))
    prompt_en = out2["storyboard"][0]["prompt_en"]
    assert out2["storyboard"][0]["mode"] == "reference"
    assert '"我" refers to <Picture 2>' in prompt_en
    assert '"破旧摩托车" refers to <Picture 3>' in prompt_en
    assert "Keep the appearance of 我, 破旧摩托车" in prompt_en


@pytest.mark.asyncio
async def test_standard_storyboard_injects_global_shot_language(monkeypatch):
    """③-1：全局运镜覆盖 LLM 每镜 camera，并追加确定性英文运镜片段。"""
    captured: list[str] = []

    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        captured.append(prompt)
        return "A rider on a snowy plain."

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)

    state = {
        "session_id": "s4",
        "script": [
            {"shot_id": 0, "visual": "骑手在雪原", "camera": "特写", "style_note": "冷色调", "duration": 6},
            {"shot_id": 1, "visual": "远景山峦", "camera": "近景", "style_note": "暖色调", "duration": 5},
        ],
        "shot_language": {"shot_size": "远景", "angle": "俯拍", "movement": "跟拍"},
    }
    out = await sb_mod.storyboarder_node(state)
    shots = out["storyboard"]

    # 两镜的 LLM camera（特写/近景）都被全局运镜替换为中文原值
    assert "远景、俯拍、跟拍" in captured[0]
    assert "特写" not in captured[0]
    assert "远景、俯拍、跟拍" in captured[1]
    assert "近景" not in captured[1]
    # 英文确定性片段追加到提示词尾部（每镜一致）
    for shot in shots:
        assert shot["prompt_en"].endswith("wide shot, high-angle shot, follow shot")
        assert shot["camera_spec"] == {"shot_size": "远景", "angle": "俯拍", "movement": "跟拍"}


@pytest.mark.asyncio
async def test_standard_storyboard_keeps_llm_camera_without_shot_language(monkeypatch):
    """③-1 回归：不给运镜倾向时保持原行为（LLM 的 camera 参与翻译，不追加英文片段）。"""
    captured: list[str] = []

    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        captured.append(prompt)
        return "A rider on a snowy plain."

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)

    state = {
        "session_id": "s5",
        "script": [{"shot_id": 0, "visual": "骑手在雪原", "camera": "特写", "style_note": "冷色调", "duration": 6}],
    }
    out = await sb_mod.storyboarder_node(state)
    shot = out["storyboard"][0]

    assert "特写" in captured[0]
    assert shot["prompt_en"] == "A rider on a snowy plain."
    assert shot["camera_spec"] == {}
