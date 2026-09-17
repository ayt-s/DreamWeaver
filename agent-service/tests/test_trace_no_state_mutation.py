"""图层埋点不得原地修改传入的 state（状态别名防护）。

`_traced` 在节点**没有返回自己的 trace** 时，`base` 就是 `state["trace"]` 那个列表
对象本身；而 `trace_util.append` 的契约是**原地追加**（`trace = append(trace, ...)`
正是靠它）。所以包装层必须先拷贝。

今天图是线性执行、语义正好就是 accumulate，所以原地改看不出来；但一旦出现并行分支，
两条路径就会共享同一个列表 —— 典型的「状态别名」，出问题时极难定位。

这条断言在修复前是**红的**：旧实现下 `state["trace"]` 会多出一条。
"""

import asyncio

from app.graph import _traced


def test_traced_does_not_mutate_input_state_trace():
    state = {"trace": [{"node": "旧条目", "status": "ok", "elapsed_ms": 1}]}
    before = list(state["trace"])

    async def node(_state):
        return {"status": None}  # 节点不返回自己的 trace → base 落到 state 那份

    out = asyncio.run(_traced("测试节点", node)(state))

    assert state["trace"] == before, "图层埋点原地改了传入 state 的 trace 列表"
    assert len(out["trace"]) == len(before) + 1, "新条目没进返回值"
    assert out["trace"][-1]["node"] == "测试节点"
    assert out["trace"][-1]["status"] == "ok"


def test_traced_appends_onto_nodes_own_trace_without_touching_it():
    """节点自己写了 trace（含逐镜条目）时：追加在**它那份的副本**上。

    LangGraph 对返回的键是「替换」语义，所以必须把副本写回 `out["trace"]` ——
    但同时不该把节点自己那个列表对象也改掉。
    """
    node_trace = [{"node": "视频生成 #1", "status": "ok", "elapsed_ms": 100}]
    state = {"trace": [{"node": "更早的条目", "status": "ok", "elapsed_ms": 1}]}

    async def node(_state):
        return {"trace": node_trace}

    out = asyncio.run(_traced("视频生成", node)(state))

    assert node_trace == [{"node": "视频生成 #1", "status": "ok", "elapsed_ms": 100}], \
        "节点自己那份 trace 被原地改了"
    assert [t["node"] for t in out["trace"]] == ["视频生成 #1", "视频生成"]
