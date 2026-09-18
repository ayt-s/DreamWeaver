"""批次 C1/C2：`state` 里的 trace 必须是极简三元组，且有长度上限。

## 锁定的行为

1. 条目**只有** `{node, status, elapsed_ms}` 三个键 —— 尤其**不能含提示词正文**
   （原实现把 `prompt_en` 塞进 `params.prompt`，与 storyboard 里那份重复进快照）
2. 所有写入都必须走 `app.utils.trace.append`（手写 `trace.append({...})` 会绕过键白名单）
3. 上限 `TRACE_MAX = 200`，超了丢最老的
4. **真实 compiled_graph 跑完**，state 里的 trace 每条都合规（锁住所有生产点）

## 口径提醒

这不是「省 Redis」的优化，实测降幅有限；理由是**链路完整性 + 可观测性**
（原来只有 3 个节点埋点，且没有任何 API 返回、前端也不渲染）。详见 `app/utils/trace.py`。
"""

import json
import pathlib
import time

import pytest

from app.state import TRACE_KEYS, TaskStatus
from app.utils import trace as trace_util

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"

#: 原重 trace 的**专属**字段名。重构后不该再出现在节点源码里（防止有人「改回来」）。
#: ⚠️ 不含 `tool_name` —— 它是 SSE 事件协议在用的键（`events.emit(..., "tool_called",
#: {"tool_name": ...})`），扫它会把正常代码判成违规（本人第一版就踩了这个误报）。
LEGACY_HEAVY_KEYS = ("latency_ms", "retry_count")


# --------------------------------------------------------------- 契约 & 助手


def test_trace_keys_is_three_fields():
    assert TRACE_KEYS == ("node", "status", "elapsed_ms")
    assert trace_util.TRACE_KEYS == TRACE_KEYS, "两处导出必须一致（state 是转发）"


def test_real_cap_is_200():
    assert trace_util.TRACE_MAX == 200


def test_append_entry_has_exactly_three_keys():
    t = trace_util.append(None, "storyboarder", trace_util.STATUS_OK, elapsed_ms=123)

    assert len(t) == 1
    assert set(t[0]) == set(TRACE_KEYS)
    assert t[0]["node"] == "storyboarder"
    assert t[0]["elapsed_ms"] == 123


def test_append_never_contains_prompt_text():
    """把提示词正文当 node 传也只会落成短字符串，结构上没有塞正文的口子。"""
    t = trace_util.append([], "video_generator", trace_util.STATUS_OK, elapsed_ms=1)

    assert "prompt" not in json.dumps(t, ensure_ascii=False)
    assert "params" not in json.dumps(t, ensure_ascii=False)


def test_append_elapsed_from_started_at():
    t = trace_util.append([], "qc_checker", trace_util.STATUS_OK, time.time() - 1.5)

    assert 1400 <= t[0]["elapsed_ms"] <= 1700


def test_append_without_started_at_is_zero():
    t = trace_util.append([], "n", trace_util.STATUS_OK)

    assert t[0]["elapsed_ms"] == 0


def test_append_clamps_negative_elapsed():
    """时钟回拨 / started_at 给了未来时间时不能产出负耗时（前端会画出倒流）。"""
    t = trace_util.append([], "n", trace_util.STATUS_OK, time.time() + 10)

    assert t[0]["elapsed_ms"] == 0


def test_append_returns_same_list():
    src: list = []

    assert trace_util.append(src, "n", trace_util.STATUS_OK) is src


def test_append_accepts_none_trace():
    assert len(trace_util.append(None, "n", trace_util.STATUS_OK)) == 1


def test_trace_cap_keeps_newest(monkeypatch):
    """Task C2：超上限丢最老的，保留最新。"""
    monkeypatch.setattr(trace_util, "TRACE_MAX", 5)
    t: list = []
    for i in range(8):
        trace_util.append(t, f"n{i}", trace_util.STATUS_OK)

    assert len(t) == 5
    assert [e["node"] for e in t] == ["n3", "n4", "n5", "n6", "n7"]


def test_shot_suffix_is_one_based():
    assert trace_util.shot("video_generator", 0) == "video_generator#1"
    assert trace_util.shot("video_generator", 9) == "video_generator#10"


# ---- 轮次后缀（@）：与逐件序号（#）是两个正交的维度 ----


def test_visit_suffix_marks_repeat_execution():
    assert trace_util.visit("qc_checker", 2) == "qc_checker@2"


def test_base_name_strips_both_suffixes():
    assert trace_util.base_name("video_generator#2") == "video_generator"
    assert trace_util.base_name("qc_checker@3") == "qc_checker"
    assert trace_util.base_name("qc_checker") == "qc_checker"


def test_per_item_entries_do_not_count_as_visits():
    """★ 逐件条目（`#k`）属于**同一次**节点执行，不能算成多次访问。

    这是实现时最容易写错的地方：视频节点的 delta 里逐件条目排在节点级条目**前面**，
    若把它们也数进去，节点级条目从一开始就会显示成「第 3 次执行」。
    """
    trace = [{"node": "video_generator#1"}, {"node": "video_generator#2"}]

    assert trace_util.visit_index(trace, "video_generator") == 0


def test_visit_index_counts_only_node_level_entries():
    trace = [
        {"node": "qc_checker"},
        {"node": "video_generator#1"},
        {"node": "qc_checker@2"},
    ]

    assert trace_util.visit_index(trace, "qc_checker") == 2
    assert trace_util.visit_index(trace, "video_generator") == 0
    assert trace_util.visit_index(trace, "nobody") == 0
    assert trace_util.visit_index(None, "qc_checker") == 0


# ------------------------------------------------------- 结构级护栏（覆盖全仓）


def test_no_module_writes_trace_directly():
    """所有 trace 写入必须走 `utils.trace.append`。

    手写 `trace.append({...})` 会绕过键白名单 —— 这正是重 trace 能长期存在的原因
    （每加一个埋点就顺手塞点 params/result）。结构级比跑一遍图更彻底：
    **连没被执行到的分支也会被检查**（image / image_slideshow 等）。
    """
    offenders = []
    for p in APP_DIR.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        # 匹配 `trace.append({` 而不是 `trace.append(` —— 后者会命中注释里的写法引用
        # （`state.py` 的说明里就有 `app.utils.trace.append()`），是误报。
        if "trace.append({" in text and p.name != "trace.py":
            offenders.append(str(p.relative_to(APP_DIR)))

    assert not offenders, f"这些文件绕过 utils.trace.append 手写 trace: {offenders}"


def test_legacy_heavy_trace_keys_are_gone():
    """原重 trace 的字段名不该再出现在节点源码里（防止有人「改回来」）。"""
    found = {}
    for p in (APP_DIR / "nodes").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        hits = [k for k in LEGACY_HEAVY_KEYS if f'"{k}"' in text]
        if hits:
            found[str(p.relative_to(APP_DIR))] = hits

    assert not found, f"节点里仍有重 trace 字段: {found}"


# ------------------------------------------------- 真实图：锁住所有生产点


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
        import asyncio
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"video_url": f"http://mock/shot_{video_id}.mp4", "video_id": video_id})
        return fut


class _FakeGateway:
    """文本返回固定 JSON；视频/图片立即成功。只为把图跑通，不带任何真实网络。"""

    async def chat(self, prompt, model=None, temperature=0.0, max_tokens=4096,
                   session_id=None) -> str:
        if "解析为结构化 Brief" in prompt:
            return ('{"theme":"产品宣传","style":"科技感","duration_seconds":"10",'
                    '"audience":"年轻用户","mood":"酷炫"}')
        if "Translate the following" in prompt:
            return "A scene, slow camera push-in"
        return ('[{"shot_id":1,"visual":"镜头一","camera":"推","duration":5,"style_note":"a"},'
                '{"shot_id":2,"visual":"镜头二","camera":"移","duration":5,"style_note":"b"}]')

    async def generate_image(self, prompt, model=None, session_id=None, size=None, ratio=None, seed=None):
        return [f"http://mock/image/{prompt[:6]}.png"]

    async def submit_video(self, prompt, model=None, seconds=None, aspect_ratio=None,
                           mode="text", reference_images=None, session_id=None,
                           first_frame=None, last_frame=None, size=None, seed=None) -> dict:
        return {"video_id": "v1", "model_name": "agnes-video-2.5-flash", "provider": "intl"}

    async def query_video(self, video_id, model_name, mode="text", provider_name=None):
        return {"status": "completed", "video_url": "http://mock/shot.mp4"}

    def bind_session(self, session_id, provider_name):
        pass

    async def close(self):
        pass


@pytest.fixture
def patched(monkeypatch):
    """装配替身：网关（**两处**）/ poller / QC 探针。

    ⚠️ `gateway` 必须同时注入 `app.nodes.*` 与 `app.tools.video` —— 工具层自己
    `from app.gateway.agnes import gateway`，只 patch 一处仍会打真实 API。
    """
    from app import gateway as gateway_mod
    from app.nodes import (image as image_mod, parser as parser_mod, qc as qc_mod,
                           script as script_mod, storyboard as storyboard_mod)
    from app.nodes import video as nodes_video_mod
    from app.tools import video as tools_video_mod

    gw = _FakeGateway()
    monkeypatch.setattr(gateway_mod, "agnes", gw)
    for mod in (parser_mod, script_mod, storyboard_mod, image_mod,
                nodes_video_mod, tools_video_mod):
        monkeypatch.setattr(mod, "gateway", gw)
    monkeypatch.setattr(nodes_video_mod, "poller", _FakePoller())

    async def _dur(path):
        return 5.0

    async def _dim(path):
        return (1280, 720)

    monkeypatch.setattr(qc_mod, "probe_duration", _dur)
    monkeypatch.setattr(qc_mod, "probe_dimensions", _dim)

    # 让 QC 稳定判失败 → 走 fix_looping → fix_give_up → notify_final（跑完最短闭环，
    # 且顺带覆盖自愈分支的埋点；本轮只关心 trace 形状）
    def _analyze(path):
        return {"total_frames": 5, "black_frame_ratio": 0.0,
                "blur_frame_ratio": 1.0, "passed": False}

    monkeypatch.setattr(qc_mod, "analyze_video_frames", _analyze)
    return gw


@pytest.mark.asyncio
async def test_repeated_node_visits_are_distinguishable(monkeypatch, patched):
    """★ 同一节点被**多次执行**时必须可区分（自愈轮次）。

    和「逐镜条目要带序号」是同一类问题：自愈开启后 `qc_checker` / `fix_looping` /
    `video_generator` 在一个任务里各会被跑好几次，若节点级条目同名，
    时间线上就是几行一模一样的「质量检查 1.2s」，看不出**哪一轮修的、第几轮才过**。

    这正是真实排障时最想知道的事（上一轮线上任务 13:07 进 fix_looping、13:12 失败，
    当时最想确认的就是"第几轮做了什么"）。
    """
    from app import graph

    # 只让第 1 镜失败（失败过半会触发成本闸门 → 一次都不修，就跑不出多轮）
    from app.nodes import qc as qc_mod

    def _analyze(path):
        bad = path.endswith("seg_000.mp4")
        return {"total_frames": 5, "black_frame_ratio": 0.0,
                "blur_frame_ratio": 1.0 if bad else 0.0, "passed": not bad}

    monkeypatch.setattr(qc_mod, "analyze_video_frames", _analyze)

    res = await graph.compiled_graph.ainvoke(
        {"session_id": "c1-repeat", "user_id": "t", "raw_prompt": "产品宣传片，10 秒",
         "gen_type": "text_video", "status": TaskStatus.PENDING,
         "fix_round": 0, "max_fix_rounds": 2, "fix_history": [],
         "trace": [], "created_at": 0, "updated_at": 0},
        config={"configurable": {"thread_id": "c1-repeat"}})

    names = [t["node"] for t in res["trace"]]
    qc = [n for n in names if n.startswith("qc_checker")]

    assert len(qc) >= 2, f"这个用例需要 QC 被跑多次，实际 trace: {names}"
    assert len(set(qc)) == len(qc), f"QC 多次执行无法区分轮次: {qc}"
    assert res["fix_round"] >= 1, f"应真的修过，实际 fix_round={res['fix_round']}"


@pytest.mark.asyncio
async def test_every_traced_node_also_emits_node_events(monkeypatch, patched):
    """★ 两个视图的口径必须一致：trace 快照里有某个节点，实时 SSE 里就该有它的事件。

    **为什么需要**：trace 由**图层包装**统一产生（结构上不会漏），而事件是
    **各节点手写**的 —— 手写就会漏。实测漏了三处：

    - `script_writer` **一个事件都不发** → 实时视图里「剧本生成」整步消失
    - `video_generator` 从不发 `node_completed` → 「视频生成」永远停在「进行中」
    - `fix_give_up` 只有 completed、没有 entered → 「放弃修复」凭空出现

    于是「全链路可见」这句话**只在 trace 快照里成立**。这条测试把两个视图绑在一起：
    以后新增节点忘了发事件会立刻红，而不是等用户发现"面板里怎么少了一步"。
    """
    from app import events as events_mod
    from app import graph

    recorded: list[tuple[str, str]] = []

    async def _record_emit(session_id, event_type, data=None, **kwargs):
        recorded.append((event_type, str((data or {}).get("node_id") or "")))
        return None

    monkeypatch.setattr(events_mod, "emit", _record_emit)

    res = await graph.compiled_graph.ainvoke(
        {"session_id": "c1-parity", "user_id": "t", "raw_prompt": "产品宣传片，10 秒",
         "gen_type": "text_video", "status": TaskStatus.PENDING,
         "fix_round": 0, "max_fix_rounds": 1, "fix_history": [],
         "trace": [], "created_at": 0, "updated_at": 0},
        config={"configurable": {"thread_id": "c1-parity"}})

    traced = {t["node"].split("#")[0].split("@")[0] for t in res["trace"]}
    entered = {nid for etype, nid in recorded if etype == "node_entered"}
    completed = {nid for etype, nid in recorded if etype == "node_completed"}

    assert traced, "跑完一张图却没有 trace，用例前提不成立"
    assert not (traced - entered), (
        f"这些节点在 trace 里有、却没发 node_entered：{sorted(traced - entered)}"
        f"（实际收到的事件：{sorted(recorded)}）")
    assert not (traced - completed), (
        f"这些节点没发 node_completed：{sorted(traced - completed)}")


@pytest.mark.asyncio
async def test_real_graph_produces_only_slim_trace(monkeypatch, patched):
    """★ 真实 compiled_graph 跑完，trace 每条都必须是三元组。"""
    from app import graph

    res = await graph.compiled_graph.ainvoke(
        {"session_id": "c1-trace", "user_id": "t", "raw_prompt": "产品宣传片，10 秒",
         "gen_type": "text_video", "status": TaskStatus.PENDING,
         "fix_round": 0, "max_fix_rounds": 1, "fix_history": [],
         "trace": [], "created_at": 0, "updated_at": 0},
        config={"configurable": {"thread_id": "c1-trace"}})

    trace = res["trace"]
    assert trace, "跑完一张图却没有任何 trace —— 埋点没生效"

    for entry in trace:
        assert set(entry) == set(TRACE_KEYS), f"trace 条目多/少字段: {entry}"
        assert isinstance(entry["elapsed_ms"], int)
        assert entry["elapsed_ms"] >= 0

    # 提示词正文绝不能出现在 trace 里（这是本次重构的核心动机）
    blob = json.dumps(trace, ensure_ascii=False)
    assert "镜头一" not in blob and "slow camera push-in" not in blob, "提示词正文泄漏进 trace"

    # 链路要真的连起来：解析 → 写脚本 → 分镜 → 生成 都在
    nodes = [e["node"] for e in trace]
    for expected in ("requirement_parser", "script_writer", "storyboarder"):
        assert any(n.startswith(expected) for n in nodes), f"{expected} 没有埋点：{nodes}"

    # 逐镜可区分（否则时间线只有一根长条）
    assert any("#" in n for n in nodes), f"没有任何带序号的轨迹，逐镜粒度丢失：{nodes}"
