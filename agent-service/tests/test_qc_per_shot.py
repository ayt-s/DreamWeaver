"""qc_checker 逐镜质检（A5）。

**背景（P0-1）**：原实现只检查 `video_urls[0]`，且判定逻辑是
`if video_path.startswith(("http://","https://")) or not os.path.exists(path)` →
直接返回 `{"passed": True, "skipped": True}`。而 `video_urls` 全是 agnes 公网直链，
全链路又没有下载步骤 → **QC 在生产环境从未真正执行过**。

A1~A4 把下载前置成 asset_fetch 节点后，QC 终于有本地文件可检。本测试锁定：
1. 逐镜出报告（不再只看第一镜）
2. 跳过分支彻底删除（`skipped` 键不得再出现）
3. 单镜异常/产物缺失不中断其它镜
4. 新增规则：时长偏离、画幅不符
"""
from pathlib import Path

import pytest

from app.nodes import qc


def _shots(seconds=("5", "5", "5"), aspect_ratio=("16:9", "16:9", "16:9")):
    return [
        {"prompt_en": f"shot{i}", "seconds": s, "aspect_ratio": a}
        for i, (s, a) in enumerate(zip(seconds, aspect_ratio))
    ]


def _patch(monkeypatch, tmp_path, analyze, duration=5.0, dims=(1280, 720)):
    """装配 analyze_video_frames / probe_duration / probe_dimensions 三个替身。"""
    async def _dur(path):
        return duration if not callable(duration) else duration(path)

    async def _dim(path):
        return dims if not callable(dims) else dims(path)

    monkeypatch.setattr(qc, "analyze_video_frames", analyze)
    monkeypatch.setattr(qc, "probe_duration", _dur)
    monkeypatch.setattr(qc, "probe_dimensions", _dim)


def _mkfiles(tmp_path, n, session="q"):
    d = tmp_path / session
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(n):
        p = d / f"seg_{i:03d}.mp4"
        p.write_bytes(b"x" * 100)
        files.append(str(p))
    return files


def _ok(p):
    return {"total_frames": 30, "black_frame_ratio": 0.0, "blur_frame_ratio": 0.0, "passed": True}


@pytest.mark.asyncio
async def test_reports_every_shot_not_just_first(monkeypatch, tmp_path):
    files = _mkfiles(tmp_path, 3)

    def analyze(p):
        # 用「空帧」触发失败：低细节（blur）自 2026-09-18 起不再判失败。
        # 这里仍保留 blur=0.9，顺带验证「参考指标照样透传给上层」。
        bad = p.endswith("001.mp4")
        return {"total_frames": 30, "black_frame_ratio": 0.0,
                "blur_frame_ratio": 0.9 if bad else 0.0,
                "flat_frame_ratio": 0.5 if bad else 0.0,
                "failed_reasons": ["flat_frames"] if bad else [],
                "passed": not bad}

    _patch(monkeypatch, tmp_path, analyze)
    state = {"session_id": "q", "local_video_paths": files, "storyboard": _shots()}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert len(rep["shots"]) == 3
    assert rep["passed"] is False
    assert rep["failed_shots"] == [1]
    assert rep["shots"][1]["blur_frame_ratio"] == 0.9
    assert "skipped" not in rep, "跳过分支必须彻底删除 —— 它是 QC 从未生效的元凶"
    assert "skipped" not in rep["shots"][0]


@pytest.mark.asyncio
async def test_all_pass_sets_passed_true(monkeypatch, tmp_path):
    files = _mkfiles(tmp_path, 2)
    _patch(monkeypatch, tmp_path, _ok)
    state = {"session_id": "q", "local_video_paths": files,
             "storyboard": _shots(seconds=("5", "5"))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["passed"] is True
    assert rep["failed_shots"] == []
    assert all(s["passed"] for s in rep["shots"])


@pytest.mark.asyncio
async def test_empty_slot_marks_failure_without_crashing(monkeypatch, tmp_path):
    """asset_fetch 下载失败的索引留空串 → QC 必须识别为「产物缺失」，不能崩。"""
    files = _mkfiles(tmp_path, 1)
    _patch(monkeypatch, tmp_path, _ok)
    state = {"session_id": "q", "local_video_paths": [files[0], ""],
             "storyboard": _shots(seconds=("5", "5"))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["passed"] is False
    assert rep["failed_shots"] == [1]
    assert rep["shots"][1]["error"]
    assert rep["shots"][0]["passed"] is True, "缺失镜不该拖累已通过的镜"


@pytest.mark.asyncio
async def test_single_shot_exception_does_not_abort_others(monkeypatch, tmp_path):
    """第 1 镜 analyze 抛异常 → 只把它记失败，其余照常检查。"""
    files = _mkfiles(tmp_path, 3)

    def analyze(p):
        if p.endswith("001.mp4"):
            raise FileNotFoundError("无法打开视频文件")
        return _ok(p)

    _patch(monkeypatch, tmp_path, analyze)
    state = {"session_id": "q", "local_video_paths": files, "storyboard": _shots()}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["failed_shots"] == [1]
    assert "无法打开" in rep["shots"][1]["error"]
    assert rep["shots"][0]["passed"] is True
    assert rep["shots"][2]["passed"] is True


@pytest.mark.asyncio
async def test_no_local_files_reports_failure(monkeypatch, tmp_path):
    """一个本地文件都没有（下载全失败）→ passed=False，且必须给出原因。"""
    _patch(monkeypatch, tmp_path, _ok)
    rep = (await qc.qc_checker_node(
        {"session_id": "q", "local_video_paths": [], "storyboard": []}))["qc_report"]

    assert rep["passed"] is False
    assert rep["shots"] == []
    assert rep["reason"]


@pytest.mark.asyncio
async def test_duration_deviation_fails_the_shot(monkeypatch, tmp_path):
    """时长规则：与 storyboard 期望相差超过容差 → 该镜不通过。"""
    files = _mkfiles(tmp_path, 2)
    # 第 0 镜期望 5s 实测 5.0（通过）；第 1 镜期望 5s 实测 12.0（超容差）
    _patch(monkeypatch, tmp_path, _ok,
           duration=lambda p: 5.0 if p.endswith("000.mp4") else 12.0)
    state = {"session_id": "q", "local_video_paths": files,
             "storyboard": _shots(seconds=("5", "5"))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["failed_shots"] == [1]
    assert rep["shots"][1]["duration"] == 12.0
    assert rep["shots"][1]["duration_expected"] == 5
    assert "时长" in rep["shots"][1]["error"]
    assert rep["shots"][0]["passed"] is True


@pytest.mark.asyncio
async def test_duration_inside_tolerance_passes_outside_fails(monkeypatch, tmp_path):
    """锁定容差语义：期望 5s，容差 1.0 → 5.8 通过、6.2 失败。"""
    assert qc.DURATION_TOLERANCE_S == 1.0, "容差常量变了要同步本用例"

    inside = _mkfiles(tmp_path, 1, session="qin")
    _patch(monkeypatch, tmp_path, _ok, duration=5.8)
    rep_in = (await qc.qc_checker_node(
        {"session_id": "qin", "local_video_paths": inside,
         "storyboard": _shots(seconds=("5",))}))["qc_report"]
    assert rep_in["shots"][0]["passed"] is True

    outside = _mkfiles(tmp_path, 1, session="qout")
    _patch(monkeypatch, tmp_path, _ok, duration=6.2)
    rep_out = (await qc.qc_checker_node(
        {"session_id": "qout", "local_video_paths": outside,
         "storyboard": _shots(seconds=("5",))}))["qc_report"]
    assert rep_out["shots"][0]["passed"] is False


@pytest.mark.asyncio
async def test_aspect_ratio_mismatch_fails_the_shot(monkeypatch, tmp_path):
    """画幅规则：竖屏任务产出了横屏 → 该镜不通过。"""
    files = _mkfiles(tmp_path, 2)
    # 期望 9:16，但第 1 镜实际是 1280x720（16:9）
    _patch(monkeypatch, tmp_path, _ok,
           dims=lambda p: (720, 1280) if p.endswith("000.mp4") else (1280, 720))
    state = {"session_id": "q", "local_video_paths": files,
             "storyboard": _shots(aspect_ratio=("9:16", "9:16"))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["shots"][0]["passed"] is True
    assert rep["failed_shots"] == [1]
    assert "画幅" in rep["shots"][1]["error"]


@pytest.mark.asyncio
async def test_probe_failure_is_not_an_error(monkeypatch, tmp_path):
    """探测失败（返回 -1）时不应把好镜判成失败 —— 探测是附加信息，不是否决项。"""
    files = _mkfiles(tmp_path, 1)
    _patch(monkeypatch, tmp_path, _ok, duration=-1.0, dims=(-1, -1))
    state = {"session_id": "q", "local_video_paths": files,
             "storyboard": _shots(seconds=("5",))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["shots"][0]["passed"] is True
    assert rep["passed"] is True


@pytest.mark.asyncio
async def test_uses_local_paths_not_video_urls(monkeypatch, tmp_path):
    """必须读 local_video_paths，绝不回退到 video_urls（回退就等于 QC 又失效）。"""
    files = _mkfiles(tmp_path, 1)
    _patch(monkeypatch, tmp_path, _ok)
    state = {
        "session_id": "q",
        "video_urls": ["http://agnes/cdn/only-remote.mp4"],  # 只有远程直链
        "local_video_paths": files,
        "storyboard": _shots(seconds=("5",)),
    }

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["total_shots"] == 1
    assert rep["shots"][0]["path"] == files[0]


# ------------------------- 判定口径（2026-09-18）：低细节只报告、不判定

@pytest.mark.asyncio
async def test_low_detail_only_is_not_a_failure(monkeypatch, tmp_path):
    """★ 只有「低细节」（blur 高、无确定性缺陷）时，该镜必须**通过**且 error 为空。

    这条锁住口径修正：全图 Laplacian 方差测的是画面细节量，实测被判「低细节过半」
    的 4 段逐帧目视 6/6 全部清晰（夜景浅景深 / 柔光人脸 / 暗场光束特效）。
    它曾让 4/44 段误报，而误报的代价是「质检未通过」这句话失去可信度。
    """
    files = _mkfiles(tmp_path, 1)

    def analyze(_p):
        return {"total_frames": 30, "black_frame_ratio": 0.0, "blur_frame_ratio": 1.0,
                "flat_frame_ratio": 0.0, "failed_reasons": [], "passed": True}

    _patch(monkeypatch, tmp_path, analyze)
    state = {"session_id": "q", "local_video_paths": files,
             "storyboard": _shots(seconds=("5",))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["shots"][0]["passed"] is True
    assert rep["shots"][0]["error"] == "", "低细节不该产出任何失败文案"
    assert rep["shots"][0]["blur_frame_ratio"] == 1.0, "参考指标仍要透传给轨迹/前端"
    assert rep["passed"] is True


@pytest.mark.asyncio
async def test_failed_reasons_are_exposed_on_the_shot_entry(monkeypatch, tmp_path):
    """★ 成因要结构化透传（`failed_reasons`），且文案必须与成因一致。

    上层（自愈挑修正后缀、轨迹面板）此前只能靠猜中文文案来分流失败原因。
    """
    files = _mkfiles(tmp_path, 1)

    def analyze(_p):
        return {"total_frames": 30, "black_frame_ratio": 0.0, "blur_frame_ratio": 0.0,
                "flat_frame_ratio": 0.5, "failed_reasons": ["flat_frames"],
                "passed": False}

    _patch(monkeypatch, tmp_path, analyze)
    state = {"session_id": "q", "local_video_paths": files,
             "storyboard": _shots(seconds=("5",))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["shots"][0]["failed_reasons"] == ["flat_frames"]
    assert "空帧" in rep["shots"][0]["error"], (
        f"文案必须说是空帧，实际: {rep['shots'][0]['error']!r}"
    )
    assert "模糊" not in rep["shots"][0]["error"], "低细节已不是失败成因，文案里不该出现"


@pytest.mark.asyncio
async def test_legacy_report_without_failed_reasons_still_attributes(monkeypatch, tmp_path):
    """兼容旧形状报告（无 `failed_reasons`，如历史数据/第三方 stub）：按比例回推成因。

    回推不出来时**不许臆造**成因 —— 宁可写「报告未给出具体判据」，
    也不能编一句可能是假的理由（本项目「文案不说谎」的规矩）。
    """
    files = _mkfiles(tmp_path, 2)

    def analyze(p):
        if p.endswith("000.mp4"):   # 空帧可回推
            return {"total_frames": 30, "black_frame_ratio": 0.0,
                    "blur_frame_ratio": 0.0, "flat_frame_ratio": 0.5, "passed": False}
        return {"total_frames": 30, "black_frame_ratio": 0.0,   # 只有 blur → 回推不出成因
                "blur_frame_ratio": 0.9, "flat_frame_ratio": 0.0, "passed": False}

    _patch(monkeypatch, tmp_path, analyze)
    state = {"session_id": "q", "local_video_paths": files,
             "storyboard": _shots(seconds=("5", "5"))}

    rep = (await qc.qc_checker_node(state))["qc_report"]

    assert rep["shots"][0]["failed_reasons"] == ["flat_frames"]
    assert "空帧" in rep["shots"][0]["error"]
    assert rep["shots"][1]["failed_reasons"] == [], "回推不出成因时不许编"
    assert "未给出具体判据" in rep["shots"][1]["error"]
