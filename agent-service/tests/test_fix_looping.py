"""fix_looping 节点 + _fix_route（B1/B2）：镜级自愈。

锁定的行为：
1. **只重生失败镜** —— 通过的镜保留 `existing_video_url`（省 agnes 额度）
2. **修正后缀写独立字段 `fix_hint`，绝不改 `prompt_en`**
   （`prompt_en` 会经 notify_final 成为 Java 的 `segments_json` = 段重生基线）
3. **中止检查** —— 用户删任务/全量重生后不再自动烧额度
4. **四道闸门**：已中止 / 无失败镜 / 轮次超限 / 失败镜过半
5. `fix_history` 追加（不覆盖）并记录 `used_hint`（供 B0 阶段 3 的在线 A/B 统计）
"""
import json

import pytest

from app.graph import _fix_give_up_node, _fix_route
from app.nodes import fix_looping as fl


def _shots(n=4, with_reuse=True):
    out = []
    for i in range(n):
        s = {"prompt_en": f"shot {i}", "seconds": "5", "aspect_ratio": "16:9",
             "cn_description": f"第 {i} 镜中文描述"}
        if with_reuse:
            s["existing_video_url"] = f"http://agnes/{i}.mp4"
        out.append(s)
    return out


def _qc(failed, shots_entries=None, total=None):
    """构造 qc_report。shots_entries 传 [{index, blur_frame_ratio, error}]。"""
    entries = shots_entries or [{"index": i, "error": ""} for i in range(total or 4)]
    return {
        "passed": not failed,
        "failed_shots": list(failed),
        "total_shots": total if total is not None else len(entries),
        "shots": entries,
        "reason": "" if not failed else f"{len(failed)} 镜未通过",
    }


def _state(failed, storyboard=None, **over):
    st = {
        "session_id": "f1",
        "storyboard": storyboard if storyboard is not None else _shots(4),
        "qc_report": _qc(failed, total=4),
        "fix_round": 0,
        "max_fix_rounds": 3,
        "fix_history": [],
    }
    st.update(over)
    return st


# ------------------------------------------------------------------ 节点行为

@pytest.mark.asyncio
async def test_only_failed_shots_lose_reuse_fields(monkeypatch):
    """通过的镜必须保留 existing_video_url —— 这是「自愈」而非「全量重生」的关键。"""
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "off", raising=False)

    out = await fl.fix_looping_node(_state(failed=[1]))
    sb = out["storyboard"]

    assert sb[0]["existing_video_url"] == "http://agnes/0.mp4"
    assert sb[2]["existing_video_url"] == "http://agnes/2.mp4"
    assert "existing_video_url" not in sb[1], "失败镜必须清掉复用字段才会真正重提"


@pytest.mark.asyncio
async def test_never_mutates_prompt_en(monkeypatch):
    """prompt_en 必须原样不动 —— 它经 segments_json 成为段重生基线。"""
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)

    out = await fl.fix_looping_node(_state(
        failed=[1],
        storyboard=_shots(2),
        qc_report=_qc([1], shots_entries=[
            {"index": 0, "error": ""},
            # ⚠️ 别再用 blur 当「标准失败原因」：它 2026-09-18 起不参与 passed，
            #    且不再映射修正后缀（见 fix_looping._pick_hint）。改用可映射的黑帧。
            {"index": 1, "error": "画面质检未通过（黑帧比例 90%）",
             "black_frame_ratio": 0.9, "failed_reasons": ["black_frames"]},
        ], total=2),
    ))

    assert out["storyboard"][1]["prompt_en"] == "shot 1", "prompt_en 被污染了"
    assert out["storyboard"][0]["prompt_en"] == "shot 0"
    # 后缀落在独立字段
    assert out["storyboard"][1]["fix_hint"].startswith(",")


@pytest.mark.asyncio
async def test_hint_replaced_not_appended_across_rounds(monkeypatch):
    """多轮修复时 fix_hint 覆盖而非叠加（否则 prompt 持续膨胀漂移）。"""
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)

    st = _state(failed=[1], storyboard=_shots(2), qc_report=_qc(
        [1], shots_entries=[
            {"index": 0, "error": ""},
            {"index": 1, "error": "画幅不符", "blur_frame_ratio": 0.0},
        ], total=2))
    out1 = await fl.fix_looping_node(st)
    hint1 = out1["storyboard"][1]["fix_hint"]

    # 第二轮：换成另一个成因（画幅 → 黑帧），后缀应当被替换而不是叠加
    st2 = dict(st)
    st2["storyboard"] = out1["storyboard"]
    st2["fix_round"] = 1
    st2["fix_history"] = out1["fix_history"]
    st2["qc_report"] = _qc([1], shots_entries=[
        {"index": 0, "error": ""},
        {"index": 1, "error": "画面质检未通过（黑帧比例 90%）",
         "black_frame_ratio": 0.9, "failed_reasons": ["black_frames"]},
    ], total=2)
    out2 = await fl.fix_looping_node(st2)
    hint2 = out2["storyboard"][1]["fix_hint"]

    assert hint1 != hint2, "第二轮换原因后后缀应被替换"
    assert hint1 not in hint2, "后缀被叠加了（应覆盖）"
    assert len(hint2) < 400


@pytest.mark.asyncio
async def test_hint_mapping_by_reason(monkeypatch):
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)

    cases = [
        # ── 新口径：成因读结构化 failed_reasons（tools/qc.py 给出），不猜文案 ──
        # 唯一允许贴画质后缀的成因是黑帧
        ({"error": "画面质检未通过（黑帧比例 62%）", "black_frame_ratio": 0.62,
          "failed_reasons": ["black_frames"]}, "well-lit"),
        # ★ 空帧：flat 阈值（方差 <1.0）严格包含在 blur（<50.0）里，所以凡是空帧
        #   必然 blur_frame_ratio 也超线。旧实现因此**必然**给它贴
        #   「slow steady camera / sharp focus」—— 病灶是纯色帧，加运镜词改不到。
        ({"error": "画面质检未通过（空帧比例 30%）", "blur_frame_ratio": 0.9,
          "failed_reasons": ["flat_frames"]}, ""),
        # ★ 下载残片：病灶是文件不完整，提示词无责 → 原样重生
        ({"error": "产物不完整（下载残留）", "blur_frame_ratio": 0.7,
          "failed_reasons": ["truncated"]}, ""),
        # 结构性问题优先于成因
        ({"error": "时长偏离（期望 5s，实测 12.0s）"}, "continuous"),
        ({"error": "画幅不符（期望 9:16，实测 1280x720）"}, "aspect"),
        # ── 兼容旧报告（无 failed_reasons）：只按黑帧比例回推 ──
        ({"error": "画面质检未通过", "black_frame_ratio": 0.9}, "well-lit"),
        # ★ 旧报告里「只有 blur 超线」不再当成因（那批正是被判为误报的那批）
        ({"error": "画面质检未通过", "blur_frame_ratio": 0.9}, ""),
        ({"error": "产物缺失，未下载成功"}, ""),          # 不知成因 → 原样重生
        ({"error": "本地文件不存在"}, ""),
    ]
    for entry, expect in [(e, exp) for e, exp in cases]:
        entry = dict(entry)
        entry["index"] = 1
        out = await fl.fix_looping_node(_state(
            failed=[1], storyboard=_shots(2),
            qc_report=_qc([1], shots_entries=[{"index": 0, "error": ""}, entry], total=2)))
        hint = out["storyboard"][1].get("fix_hint", "")
        if expect:
            assert expect in hint, f"{entry.get('error')} → 期望含 {expect}，实际 {hint!r}"
        else:
            assert hint == "", f"{entry.get('error')} → 不该加后缀，实际 {hint!r}"


@pytest.mark.asyncio
async def test_abort_stops_repair_without_touching_storyboard(monkeypatch):
    """已中止 → 不重生、不动 storyboard（否则每轮都是白烧的 agnes 调用）。"""
    from app import abort

    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)
    abort.mark("f1")
    try:
        out = await fl.fix_looping_node(_state(failed=[1]))
    finally:
        abort.clear("f1")

    assert out["fix_aborted"] is True
    assert "storyboard" not in out, "中止时不应改动 storyboard"
    assert out["fix_round"] == 0, "中止时不应消耗轮次"


@pytest.mark.asyncio
async def test_fix_history_appended_with_used_hint(monkeypatch):
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)

    st = _state(failed=[1], fix_history=[{"round": 1, "failed_shots": [0]}])
    st["fix_round"] = 1
    out = await fl.fix_looping_node(st)

    assert len(out["fix_history"]) == 2, "必须是追加而不是覆盖"
    last = out["fix_history"][-1]
    assert last["round"] == 2
    assert last["failed_shots"] == [1]
    assert last["used_hint"] is True
    assert last["hint_mode"] == "mechanism"
    assert out["fix_round"] == 2


@pytest.mark.asyncio
async def test_mode_off_clears_reuse_but_adds_no_hint(monkeypatch):
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "off", raising=False)

    out = await fl.fix_looping_node(_state(
        failed=[1], storyboard=_shots(2),
        qc_report=_qc([1], shots_entries=[
            {"index": 0, "error": ""},
            {"index": 1, "error": "画面质检未通过", "blur_frame_ratio": 0.9},
        ], total=2)))

    assert "existing_video_url" not in out["storyboard"][1]
    assert "fix_hint" not in out["storyboard"][1]
    assert out["fix_history"][-1]["used_hint"] is False


@pytest.mark.asyncio
async def test_random50_arm_is_consistent_within_a_round(monkeypatch):
    """random50 下同一轮必须是**同一个分组**（否则两组不可比）。

    ⚠️ `used_hint` 语义是「本轮分配到哪一组」，不是「每个镜都写了后缀」：
    后缀是否真的写入还取决于该镜有没有可判定的成因（如产物缺失就无后缀）。
    所以断言的是「arm 决定是否写入」而不是「写入必为真」。
    """
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "random50", raising=False)

    # 给两个失败镜都配上**可判定**的成因（两条都是黑帧），这样 arm 与写入应当一致
    def mk():
        st = _state(failed=[1, 2])
        st["qc_report"] = _qc([1, 2], shots_entries=[
            {"index": 0, "error": ""},
            {"index": 1, "error": "画面质检未通过（黑帧比例 90%）",
             "black_frame_ratio": 0.9, "failed_reasons": ["black_frames"]},
            {"index": 2, "error": "画面质检未通过（黑帧比例 90%）",
             "black_frame_ratio": 0.9, "failed_reasons": ["black_frames"]},
            {"index": 3, "error": ""},
        ], total=4)
        return st

    seen_arms = set()
    for _ in range(40):
        out = await fl.fix_looping_node(mk())
        used = out["fix_history"][-1]["used_hint"]
        applied = ["fix_hint" in out["storyboard"][i] for i in (1, 2)]
        assert applied == [used, used], (
            f"同轮内分组不一致（used_hint={used}, applied={applied}）→ A/B 数据不可比")
        seen_arms.add(used)

    assert seen_arms == {True, False}, "40 次都没出现另一种分组，随机分配有问题"


@pytest.mark.asyncio
async def test_invalid_mode_falls_back_to_random50(monkeypatch):
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "bogus", raising=False)
    out = await fl.fix_looping_node(_state(failed=[1]))
    assert out["fix_history"][-1]["hint_mode"] == "random50"


@pytest.mark.asyncio
async def test_no_failed_shots_is_a_noop(monkeypatch):
    out = await fl.fix_looping_node(_state(failed=[]))
    assert "storyboard" not in out
    assert out["fix_round"] == 0 if "fix_round" in out else True


# ------------------------------------------------- 闸门（decide_repair 是唯一实现）

def test_repair_allowed_under_round_limit():
    ok, reason = fl.decide_repair(_state(failed=[1], fix_round=1))
    assert ok is True and reason == ""


def test_repair_denied_when_rounds_used_up():
    """max_fix_rounds=3：fix_round=2 时还可再修一轮（第 3 轮），到 3 就停。"""
    assert fl.decide_repair(_state(failed=[1], fix_round=2, max_fix_rounds=3))[0] is True
    ok, reason = fl.decide_repair(_state(failed=[1], fix_round=3, max_fix_rounds=3))
    assert ok is False and "轮次上限" in reason


def test_repair_denied_when_aborted():
    ok, reason = fl.decide_repair(_state(failed=[1], fix_aborted=True))
    assert ok is False and "中止" in reason


def test_repair_denied_without_failed_shots():
    assert fl.decide_repair(_state(failed=[]))[0] is False


def test_repair_denied_when_majority_failed():
    """4 镜里坏 3 镜（> ceil(4/2)=2）→ 直接放弃，不修。"""
    ok, reason = fl.decide_repair(_state(failed=[0, 1, 2]))
    assert ok is False and "超过半数" in reason


def test_repair_gate_does_not_use_shot_count_field():
    """⚠️ 镜数必须来自 storyboard。shot_count 是用户请求参数，
    未填时缺失/None，用它会让闸门静默失效 → 无限烧钱。"""
    # shot_count 故意填成很大（暗示「镜数很多、坏 3 个没关系」），
    # 但真实 storyboard 只有 4 镜 → 仍应拒绝
    assert fl.decide_repair(_state(failed=[0, 1, 2], shot_count=100))[0] is False

    # 反向：shot_count 故意填成 1（暗示「坏 3 个太多了」），
    # 真实 storyboard 有 8 镜 → 应放行
    assert fl.decide_repair(_state(failed=[0, 1, 2], storyboard=_shots(8),
                                  shot_count=1))[0] is True


# ------------------------------------------------- 路由（只读结论）

def test_route_reads_decision_and_nothing_else():
    assert _fix_route(_state(failed=[1], fix_give_up=False)) == "retry"
    assert _fix_route(_state(failed=[1], fix_give_up=True)) == "give_up"


@pytest.mark.asyncio
async def test_route_never_retries_when_node_did_not_work(monkeypatch):
    """**防无限循环的核心不变式**：节点判定放弃时不改 storyboard，
    此时路由必须给 give_up。

    历史事故：闸门写在节点和路由两处 → 节点空转（不改 storyboard、fix_round
    不再增长）+ 路由仍 retry → 死循环，实测
    `GraphRecursionError: Recursion limit of 10007`。
    现在闸门唯一实现在 decide_repair，路由只读 fix_give_up，本测试锁定该契约。
    """
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)

    # 轮次已用尽
    st = _state(failed=[1], fix_round=3, max_fix_rounds=3)
    before = json.dumps(st["storyboard"], sort_keys=True)
    out = await fl.fix_looping_node(st)
    st.update(out)

    assert json.dumps(st["storyboard"], sort_keys=True) == before, "不该改动 storyboard"
    assert _fix_route(st) == "give_up", "节点没干活，路由却要 retry → 会死循环"

    # 成本闸门
    st2 = _state(failed=[0, 1, 2])
    out2 = await fl.fix_looping_node(st2)
    st2.update(out2)
    assert _fix_route(st2) == "give_up"


@pytest.mark.asyncio
async def test_route_retries_when_node_actually_worked(monkeypatch):
    """反面契约：节点真改了 storyboard，路由必须 retry（否则自愈不生效）。"""
    monkeypatch.setattr(fl.settings, "fix_hint_mode", "mechanism", raising=False)
    st = _state(failed=[1], fix_round=0, max_fix_rounds=3)
    st.update(await fl.fix_looping_node(st))

    assert st["storyboard"][1].get("regenerate") is True
    assert _fix_route(st) == "retry"


# ------------------------------------------------------------------ 放弃节点

@pytest.mark.asyncio
async def test_give_up_marks_state_and_reason():
    st = _state(failed=[1, 2], fix_round=4,
                fix_history=[{"round": i} for i in range(1, 5)],
                fix_give_up_reason="自动修复已达轮次上限（3 轮）")
    out = await _fix_give_up_node(st)

    assert out["fix_give_up"] is True
    assert "4 轮" in out["fix_give_up_reason"]
    assert "2 镜" in out["fix_give_up_reason"]


@pytest.mark.asyncio
async def test_give_up_reports_abort_reason():
    out = await _fix_give_up_node(_state(failed=[1], fix_aborted=True))
    assert "中止" in out["fix_give_up_reason"]
