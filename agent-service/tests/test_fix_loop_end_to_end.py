"""B3 端到端：fix_looping 自愈循环跑在**真实 compiled_graph** 上。

为什么不用真实 agnes 跑（计划里 B3 的原方案）：那样要花 1 次初始 + 3 轮重生
× N 镜的真实额度，且 QC 得靠「把阈值调到 1e9」这种脏手法逼迫失败。
用 mock 网关 + 稳定失败的 QC 更可控、可重复，而且能永久留作回归护栏。

锁定的行为：
1. 循环真的转起来了：初始 1 次 + 3 轮重生（max_fix_rounds=3），之后收敛
2. **只重生失败镜**（不是全量重生）—— 提交次数按镜计
3. 终止条件可靠：轮次用尽 → fix_give_up → notify_final，**不会无限循环**
4. 终态仍是 completed（已通过的镜可用），error_message 说明修复失败情况
5. 成本闸门：失败镜过半 → 一次重生都不发生，直接放弃
"""
import asyncio

import pytest

from app import graph
from app.graph import _fix_route, _qc_route
from app.state import TaskStatus

# --------------------------------------------------------------------- 替身


class _FakePoller:
    def __init__(self):
        self.pending_tasks: dict = {}

    async def start(self):
        pass

    async def stop(self):
        pass

    async def submit(self, video_id, model_name, session_id, shot_index, provider="intl"):
        return None

    def get_future(self, video_id):
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"video_url": f"http://mock/shot_{video_id}.mp4",
                        "video_id": video_id})
        return fut


class CountingGateway:
    """文本返回固定 JSON；submit_video 计数（用来断言「只重生失败镜」）。"""

    def __init__(self):
        self.video_submits = 0
        self.submitted_prompts: list[str] = []

    async def chat(self, prompt, model=None, temperature=0.0, max_tokens=4096,
                   session_id=None) -> str:
        if "解析为结构化 Brief" in prompt:
            return ('{"theme":"产品宣传","style":"科技感","duration_seconds":"10",'
                    '"audience":"年轻用户","mood":"酷炫"}')
        if "Translate the following" in prompt:
            return "A scene, slow camera push-in"
        # 分镜：2 镜
        return ('[{"shot_id":1,"visual":"镜头一","camera":"推","duration":5,"style_note":"a"},'
                '{"shot_id":2,"visual":"镜头二","camera":"移","duration":5,"style_note":"b"}]')

    async def generate_image(self, prompt, model=None, session_id=None, size=None, ratio=None, seed=None):
        return [f"http://mock/image/{prompt[:6]}.png"]

    async def submit_video(self, prompt, model=None, seconds=None, aspect_ratio=None,
                           mode="text", reference_images=None, session_id=None,
                           first_frame=None, last_frame=None, size=None, seed=None) -> dict:
        self.video_submits += 1
        self.submitted_prompts.append(str(prompt))
        return {"video_id": f"vid{self.video_submits}",
                "model_name": "agnes-video-2.5-flash", "provider": "intl"}

    async def query_video(self, video_id, model_name, mode="text", provider_name=None):
        return {"status": "completed", "video_url": "http://mock/shot.mp4"}

    def bind_session(self, session_id, provider_name):
        pass

    async def close(self):
        pass


@pytest.fixture
def patched(monkeypatch):
    """装配所有替身：网关 / poller / QC 探测 / 修正后缀策略。

    ⚠️ `gateway` 必须同时注入 **两处**：`app.nodes.video`（节点）与
    `app.tools.video`（工具层）—— 工具层自己 `from app.gateway.agnes import gateway`，
    只 patch 节点那一份的话，工具层仍打真实 API（实测会挂住等 31s 退避重试）。
    """
    from app import gateway as gateway_mod
    from app.nodes import (fix_looping as fl, image as image_mod,
                           parser as parser_mod, qc as qc_mod,
                           script as script_mod, storyboard as storyboard_mod)
    from app.nodes import video as nodes_video_mod
    from app.tools import video as tools_video_mod

    gw = CountingGateway()
    monkeypatch.setattr(gateway_mod, "agnes", gw)
    for mod in (parser_mod, script_mod, storyboard_mod, image_mod,
                nodes_video_mod, tools_video_mod):
        monkeypatch.setattr(mod, "gateway", gw)
    monkeypatch.setattr(nodes_video_mod, "poller", _FakePoller())

    # 探测不打 ffmpeg（asset_fetch 的产物是 1KB 占位文件）
    async def _dur(path):
        return 5.0

    async def _dim(path):
        return (1280, 720)

    monkeypatch.setattr(qc_mod, "probe_duration", _dur)
    monkeypatch.setattr(qc_mod, "probe_dimensions", _dim)

    # 定死后缀策略，让断言确定
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)
    # ★ 自愈循环现在是**显式开关**（`AGENT_QC_AUTOFIX` 默认关：质检阈值未标定，
    #   实测误报率远高于项目自定的 20% 门槛，默认不允许自动重生花钱）。
    #   本文件的用例正是在测那条循环，所以这里显式打开。
    monkeypatch.setattr(fl.settings, "qc_autofix", True, raising=False)
    return gw


def _state(sid="e2e-fix", max_rounds=3):
    return {
        "session_id": sid, "user_id": "t",
        "raw_prompt": "产品宣传片，10 秒", "gen_type": "text_video",
        "status": TaskStatus.PENDING,
        "fix_round": 0, "max_fix_rounds": max_rounds, "fix_history": [],
        "trace": [], "created_at": 0, "updated_at": 0,
    }


def _qc_fails_only(monkeypatch, failing_names: set[str]):
    """让 QC 只对文件名命中 failing_names 的镜判失败（其余通过）。

    ⚠️ 失败的镜必须带**真实的确定性成因**（`failed_reasons`）：2026-09-18 起
    `blur_frame_ratio` 既不参与 `passed`、也不再映射修正后缀，拿它当「标准失败形状」
    会让「后缀有没有被拼进提交提示词」这条断言落空（那正是本文件要钉的链路）。
    这里用黑帧 —— 唯一会映射出画质后缀的成因。
    """
    from app.nodes import qc as qc_mod

    def analyze(path):
        bad = any(path.endswith(n) for n in failing_names)
        if not bad:
            return {"total_frames": 5, "black_frame_ratio": 0.0, "blur_frame_ratio": 0.0,
                    "flat_frame_ratio": 0.0, "passed": True, "failed_reasons": []}
        return {"total_frames": 5, "black_frame_ratio": 0.9, "blur_frame_ratio": 1.0,
                "flat_frame_ratio": 0.0, "passed": False,
                "failed_reasons": ["black_frames"]}

    monkeypatch.setattr(qc_mod, "analyze_video_frames", analyze)


# --------------------------------------------------------------------- 用例

@pytest.mark.asyncio
async def test_self_heal_loop_retries_only_failed_shot(monkeypatch, patched):
    """2 镜里只有 shot0 坏 → 初始 2 次提交 + 3 轮各重生 1 镜 = 5 次。"""
    _qc_fails_only(monkeypatch, {"seg_000.mp4"})
    gw = patched

    res = await graph.compiled_graph.ainvoke(
        _state(), config={"configurable": {"thread_id": "e2e-fix"}})

    # 1) 循环跑了 3 轮后收敛
    assert res["fix_round"] == 3, f"应修复 3 轮，实际 {res['fix_round']}"
    assert len(res["fix_history"]) == 3, "fix_history 应记录 3 轮（不虚记空转的那次）"
    assert all(h["used_hint"] for h in res["fix_history"])

    # 2) **只重生失败镜**：初始 2 镜 + 3 轮 × 1 镜 = 5（若全量重生会是 2+6=8）
    assert gw.video_submits == 5, (
        f"提交次数 {gw.video_submits}（预期 5）—— 偏大说明做了全量重生而不是镜级重生")

    # 3) 终止条件可靠，且如实上报
    assert res.get("fix_give_up") is True
    assert res["final_notified"] is True
    assert res["status"] == TaskStatus.COMPLETED, "已通过的镜可用 → 不标 failed"
    assert "3 轮" in res["fix_give_up_reason"]


@pytest.mark.asyncio
async def test_loop_terminates_not_infinite(monkeypatch, patched):
    """即使 QC 永远判失败，循环也必须靠轮次上限收敛（不是靠运气）。"""
    _qc_fails_only(monkeypatch, {"seg_000.mp4"})
    gw = patched

    await asyncio.wait_for(
        graph.compiled_graph.ainvoke(
            _state("e2e-term", max_rounds=2),
            config={"configurable": {"thread_id": "e2e-term"}}),
        timeout=60,   # 真死循环会超时，这条断言就是护栏
    )

    # 初始 2 + 2 轮 × 1 = 4
    assert gw.video_submits == 4


@pytest.mark.asyncio
async def test_majority_failure_gives_up_without_any_repair(monkeypatch, patched):
    """2 镜全坏 → len(failed)=2 > ceil(2/2)=1 → 一次重生都不该发生。"""
    _qc_fails_only(monkeypatch, {"seg_000.mp4", "seg_001.mp4"})
    gw = patched

    res = await graph.compiled_graph.ainvoke(
        _state("e2e-major"), config={"configurable": {"thread_id": "e2e-major"}})

    assert gw.video_submits == 2, f"成本闸门失效：发生了 {gw.video_submits} 次提交"
    assert res["fix_round"] == 0
    assert res["fix_history"] == []
    assert res.get("fix_give_up") is True


@pytest.mark.asyncio
async def test_hint_does_not_leak_into_segments_json(monkeypatch, patched):
    """修正后缀不能污染 prompt_en —— 它会经 segments_json 成为段重生基线。"""
    _qc_fails_only(monkeypatch, {"seg_000.mp4"})
    gw = patched

    res = await graph.compiled_graph.ainvoke(
        _state("e2e-hint"), config={"configurable": {"thread_id": "e2e-hint"}})

    sb = res["storyboard"]
    # 传给 Java 的 storyboard 里 prompt_en 必须干净
    assert all("well-lit" not in str(s.get("prompt_en") or "") for s in sb)
    assert all("no dark or black frames" not in str(s.get("prompt_en") or "") for s in sb)
    # 而实际提交给 agnes 的提示词应当带上了后缀（证明后缀确实生效过）
    assert any("well-lit" in p for p in gw.submitted_prompts), (
        "fix_hint 没有被拼进提交的提示词")


@pytest.mark.asyncio
async def test_all_pass_never_enters_fix_loop(monkeypatch, patched):
    """全部通过 → 不进制修循环，直接 notify_final。"""
    _qc_fails_only(monkeypatch, set())
    gw = patched

    res = await graph.compiled_graph.ainvoke(
        _state("e2e-clean"), config={"configurable": {"thread_id": "e2e-clean"}})

    assert gw.video_submits == 2
    assert res["fix_round"] == 0
    assert res.get("qc_report", {}).get("passed") is True
    assert res["status"] == TaskStatus.COMPLETED
    assert not res.get("fix_give_up")
