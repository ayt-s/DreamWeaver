"""失败镜次的「索引对齐」与「缺段告知」（真跑任务 81 暴露的两个缺陷）。

## 缺陷 1：`video_generator` 压缩数组，破坏索引对齐

原实现 `video_urls = [url_by_index[i] for i in sorted(url_by_index)]` —— 有段提交
失败时数组被**压缩**，但下游全部按「下标 == 镜号」取值：

- `asset_fetch` 的文档把这条写成硬约束（「与 video_urls 严格同长同序，不压缩数组，
  否则下游按索引取段会整体错位」，并引用历史提交 949cf41）；
- `image.py` 对图片早就用空串占位（见 test_video_rework 的图片用例）；
- 实测任务 81：只有第 2 段成功，QC 却报「第 1 镜：画幅不符」——`qc_checker` 用
  `enumerate(local_video_paths)` 当镜号，压缩后它指的其实是第 2 段；
- Java `parseResultUrls` 拿到压缩数组 → 段数与 `segments_json` 不匹配 →
  「重新生成」会把**成功那段的视频复用给失败那段**（更隐蔽的错配）。

## 缺陷 2：`notify_final` 短路丢掉「少了一段」

原实现 `error_message = summarize_qc_report(qc) or video_error`：只要 QC 有任何
结论（哪怕只是一条画幅不符），生成阶段的 `video_error`（「第 1 段提交失败…」）
就被整个吞掉 —— 用户看到「未通过质检」，完全不知道少了一段，成片还被当成
正常 completed（静默降级）。
"""
import asyncio

import pytest

from app.state import TaskStatus


# --------------------------------------------------------------------------
# video_generator：对齐 + 失败段不被当作已完成
# --------------------------------------------------------------------------

class _FakePoller:
    """future 直接已完成，URL 由 video_id 推导便于断言顺序。"""

    def get_future(self, video_id):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({
            "video_url": f"http://mock/new/{video_id}.mp4",
            "video_id": video_id,
        })
        return fut


def _shot(idx: int) -> dict:
    return {
        "shot_id": idx + 1,
        "prompt_en": f"prompt-{idx}",
        "seconds": "5",
        "aspect_ratio": "16:9",
        "mode": "text",
        "reference_images": [],
    }


def _make_state(storyboard, video_urls=None, video_ids=None):
    state = {
        "session_id": "align-test",
        "user_id": "tester",
        "raw_prompt": "失败段对齐",
        "gen_type": "image_video",
        "status": TaskStatus.VIDEO_GENERATING,
        "storyboard": storyboard,
        "trace": [],
    }
    if video_urls is not None:
        state["video_urls"] = video_urls
    if video_ids is not None:
        state["video_ids"] = video_ids
    return state


@pytest.fixture
def patch_video(monkeypatch):
    """mock 掉 generate_video_tool / poller；第 1 段（index 0）提交必失败。

    submit_calls 记录真正发起提交的镜头索引 —— 用来断言「空串占位的失败段
    不会被当成已完成而跳过」。
    """
    from app.nodes import video as video_mod

    submit_calls: list[int] = []

    async def _fake_generate_video_tool(prompt, seconds, mode, aspect_ratio,
                                        reference_images, session_id,
                                        shot_index, model=None,
                                        first_frame=None, last_frame=None) -> dict:
        submit_calls.append(shot_index)
        if shot_index == 0:
            raise RuntimeError("所有 provider 重试耗尽（read timeout）")
        return {"video_id": f"vid{shot_index}", "status": "pending"}

    monkeypatch.setattr(video_mod, "generate_video_tool", _fake_generate_video_tool)
    monkeypatch.setattr(video_mod, "poller", _FakePoller())
    return submit_calls


@pytest.mark.asyncio
async def test_failed_submit_leaves_empty_placeholder(patch_video):
    """2 段、第 1 段提交失败 → 数组仍为 2 项，失败位是空串（不压缩）。"""
    from app.nodes.video import video_generator_node

    state = _make_state([_shot(0), _shot(1)])
    result = await video_generator_node(state)

    assert result["video_urls"] == ["", "http://mock/new/vid1.mp4"]
    assert result["video_ids"] == ["", "vid1"]
    # 失败原因必须留在 state（供 notify_final 带给 Java）
    assert "第 1 段提交失败" in result["video_error"]


@pytest.mark.asyncio
async def test_empty_placeholder_is_not_treated_as_done(patch_video):
    """空串占位的镜次**不是**「已完成」→ 必须重新提交（否则重生永远空转）。"""
    from app.nodes.video import video_generator_node

    # 对齐数组：索引 0 是空串（上一轮失败留下的占位）
    state = _make_state([_shot(0), _shot(1)], video_urls=[""], video_ids=[""])
    result = await video_generator_node(state)

    assert 0 in patch_video, "空串占位的镜次被错误跳过，没有重新提交"
    assert 1 in patch_video, "第 2 段本无产物，也必须提交"
    assert len(result["video_urls"]) == 2


@pytest.mark.asyncio
async def test_existing_url_still_skips_resubmit(patch_video):
    """反向守卫：真有产物的镜次仍要跳过，不能因为判据改动就重复烧额度。"""
    from app.nodes.video import video_generator_node

    state = _make_state([_shot(0), _shot(1)],
                        video_urls=["http://mock/old/done0.mp4"],
                        video_ids=["old-vid0"])
    result = await video_generator_node(state)

    assert patch_video == [1], "已有产物的第 1 段被重复提交"
    assert result["video_urls"][0] == "http://mock/old/done0.mp4"


# --------------------------------------------------------------------------
# notify_final：缺段必须说出来
# --------------------------------------------------------------------------

def _spy(monkeypatch):
    calls = []

    async def fake_notify(session_id=None, status=None, video_urls=None,
                          error_message=None, **kwargs):
        calls.append({
            "session_id": session_id, "status": status,
            "video_urls": list(video_urls or []), "error_message": error_message,
        })

    monkeypatch.setattr("app.callback.java_notify.notify_java_completion", fake_notify)
    return calls


def _qc(passed, failed=(), shots=(), total=None):
    return {
        "passed": passed,
        "failed_shots": list(failed),
        "total_shots": total if total is not None else len(shots),
        "shots": list(shots),
        "reason": "",
    }


@pytest.mark.asyncio
async def test_all_empty_urls_reports_failed(monkeypatch):
    """全空串（对齐数组，但一段都没产物）→ 必须判 failed，不能因列表非空就当完成。"""
    calls = _spy(monkeypatch)
    from app.nodes import notify_final as nf

    state = {
        "session_id": "n-align-1",
        "video_urls": ["", ""],
        "video_error": "第 1 段提交失败: read timeout；第 2 段提交失败: read timeout",
    }
    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_missing_shot_is_spelled_out_alongside_qc(monkeypatch):
    """有产物但少一段 → 「缺少第 N 镜」+ QC 结论 + 生成错误，三者都要带上。"""
    calls = _spy(monkeypatch)
    from app.nodes import notify_final as nf

    state = {
        "session_id": "n-align-2",
        "video_urls": ["", "http://a/1.mp4"],
        "video_error": "第 1 段提交失败: 所有 provider 重试耗尽",
        "qc_report": _qc(False, failed=[1], shots=[{"index": 1, "passed": False,
                                                    "error": "画幅不符（期望 16:9，实测 704x704）"}]),
    }
    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    msg = calls[0]["error_message"]
    assert calls[0]["status"] == "completed"
    assert "1/2 镜有产物" in msg and "缺少第 1 镜" in msg     # 缺段（对齐后镜号是真实镜号）
    assert "第 1 段提交失败" in msg                           # 生成阶段原因（原来被吞掉）
    assert "第 2 镜：画幅不符" in msg                         # QC 结论（原来独占）


@pytest.mark.asyncio
async def test_no_missing_notice_when_all_shots_have_video(monkeypatch):
    """反向守卫：全都有产物时不许出现「缺少第 N 镜」这种噪音。"""
    calls = _spy(monkeypatch)
    from app.nodes import notify_final as nf

    state = {
        "session_id": "n-align-3",
        "video_urls": ["http://a/0.mp4", "http://a/1.mp4"],
        "segments": [{"prompt": "a"}, {"prompt": "b"}],   # 画布模式，不走自动拼接
        "qc_report": _qc(True),
    }
    await nf.notify_final_node(state)
    await asyncio.sleep(0)

    assert calls[0]["error_message"] is None
    assert calls[0]["video_urls"] == ["http://a/0.mp4", "http://a/1.mp4"]


# --------------------------------------------------------------------------
# qc_checker：对齐后镜号与 storyboard 逐镜配对
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qc_pairs_shot_by_index_after_alignment(monkeypatch):
    """空串占位下 QC 的第 i 条报告必须对应 storyboard[i]（压缩时会整体错位）。

    断言用的是 `aspect_ratio`：它是**从 storyboard 取期望值**再和实测比对的字段，
    所以它跟第几条报告对上，就证明配对用了同一个下标。
    """
    from app.nodes.qc import qc_checker_node

    state = {
        "session_id": "qc-align",
        "status": TaskStatus.QC_CHECKING,
        "storyboard": [
            {"seconds": "5", "aspect_ratio": "9:16"},
            {"seconds": "8", "aspect_ratio": "16:9"},
        ],
        # 与 storyboard 等长对齐：第 1 段失败留空串，第 2 段同理（本用例只验配对）
        "local_video_paths": ["", ""],
        "trace": [],
    }
    out = await qc_checker_node(state)
    report = out["qc_report"]

    assert [s["index"] for s in report["shots"]] == [0, 1]
    assert report["shots"][0]["aspect_ratio"] == "9:16"
    assert report["shots"][1]["aspect_ratio"] == "16:9"
    assert report["shots"][0]["duration_expected"] == 5
    assert report["shots"][1]["duration_expected"] == 8
    # 空串占位 = 产物缺失，必须显式判失败（不能静默当成通过）
    assert report["shots"][0]["error"] == "产物缺失，未下载成功"
    assert report["failed_shots"] == [0, 1]
