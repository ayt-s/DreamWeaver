"""app/tools/qc.py 的采样逻辑回归测试。

## 锁定的真实 bug（A6 标定实测发现）

`analyze_video_frames` 原实现的计数写错了：

    total = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if total % frame_interval == 0:
            total += 1          # ← 只在实际采样时自增
            ...分析...

`total` 从 0 开始，条件在首帧成立 → total 变 1；此后 `1 % 30 != 0` 永远不成立，
**采样计数再也不会自增**，于是整个循环只分析了第一帧，`total_frames` 恒为 1。

后果（实测数据）：21 个真实段里 `blur_frame_ratio` 呈「0 或 1.0」的双峰，
6 个段被判「整段 100% 模糊」——那其实是「第一帧模糊」。
一个 5~10 秒的视频被拿第一帧就下结论，这个 bug 比阈值不准更致命。

修法：用独立的帧计数器判定采样点，采样计数只在采样时自增。
"""
import cv2
import numpy as np
import pytest

from app.tools import qc


class _FakeCapture:
    """假 VideoCapture：逐帧返回预设图像，模拟任意 fps / 帧数。

    `declared` 让「容器声明的总帧数」与「实际能解出的帧数」可以不一致 ——
    这是模拟**下载残片**的唯一办法（真实残片就是声明 107 帧、只能解出 11 帧）。
    """

    def __init__(self, frames: list[np.ndarray], fps: float = 30.0,
                 declared: int | None = None):
        self._frames = frames
        self._i = 0
        self._fps = fps
        self._declared = len(frames) if declared is None else declared
        self.released = False

    def isOpened(self) -> bool:
        return True

    def get(self, prop):
        if prop == qc.cv2.CAP_PROP_FRAME_COUNT:
            return float(self._declared)
        return self._fps

    def read(self):
        if self._i >= len(self._frames):
            return False, None
        f = self._frames[self._i]
        self._i += 1
        return True, f

    def release(self) -> None:
        self.released = True


def _sharp(h=64, w=64) -> np.ndarray:
    """高 Laplacian 方差：随机噪声（逐像素跳变）。"""
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


def _flat(h=64, w=64) -> np.ndarray:
    """**空帧**：纯色 → Laplacian 方差 0，画面里没有任何内容。

    ⚠️ 与「低细节」是两件事（2026-09-18 拆开）：纯色/全黑帧现在走独立的
    `flat_frame_ratio`（零容忍），而「低细节但正常」的内容见 `_low_detail()`。
    """
    return np.full((h, w, 3), 128, dtype=np.uint8)


def _low_detail(h=64, w=64) -> np.ndarray:
    """**低细节但不空**：低频正弦条纹 → 方差落在「空帧」与「清晰」之间。

    这是真实内容里最常见的争议形态（实测：柔光人脸特写方差 3~4、夜间浅景深 11~20）——
    它会被记进 blur 桶，但**不该**被当成空帧。
    """
    xx, _ = np.meshgrid(np.arange(w), np.arange(h))
    img = (128 + 60 * np.sin(xx / 6.0))[..., None].repeat(3, axis=2).astype(np.uint8)
    var = float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    assert qc.FLAT_VARIANCE_THRESHOLD < var < qc.BLUR_VARIANCE_THRESHOLD, (
        f"夹具失效：低细节样本的方差 {var} 不在（{qc.FLAT_VARIANCE_THRESHOLD}, "
        f"{qc.BLUR_VARIANCE_THRESHOLD}）区间内 —— 夹具会失去区分力")
    return img


def _dark(h=64, w=64) -> np.ndarray:
    """**黑帧但不空**：全像素 < 10（构成黑帧）而带噪声（方差 > 模糊阈值）。

    用来单独验证「黑帧」这条规则 —— 纯黑的帧会同时命中空帧规则，分不出是哪条在起作用。
    """
    img = np.random.default_rng(7).integers(0, 10, size=(h, w, 3), dtype=np.uint8)
    var = float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
    assert var > qc.BLUR_VARIANCE_THRESHOLD, f"夹具失效：暗噪声方差 {var} 太低会同时算模糊"
    return img


@pytest.fixture
def patch_capture(monkeypatch):
    def _install(frames, fps=30.0, declared=None):
        cap = _FakeCapture(frames, fps, declared=declared)
        monkeypatch.setattr(qc.cv2, "VideoCapture", lambda _p: cap)
        return cap
    return _install


def test_samples_every_frame_when_interval_is_one(patch_capture):
    """fps=30、interval=1 → frame_interval=30 → 60 帧视频应采样 2 帧（0 与 30）。

    修复前 total_frames 恒为 1（只看首帧）。
    """
    patch_capture([_sharp() for _ in range(60)], fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2, (
        f"应采样 2 帧（第 0、30 帧），实际 {r['total_frames']} —— "
        f"为 1 说明又退回「只看首帧」的老 bug"
    )


def test_blur_ratio_uses_all_sample_points_not_first(patch_capture):
    """首帧清晰、第 30 帧低细节 → 结论必须反映「有模糊帧」，不能只看首帧就放行。

    修复前 `total_frames` 恒为 1，这个视频会得到 blur=0.0、passed=True（漏检）。
    ⚠️ 这里用 `_low_detail()`（低频内容，方差 ~8）而不是纯色帧 —— 纯色是「空帧」，
    走的是另一条零容忍规则（见 test_empty_frame_... 系列）。
    """
    frames = [_sharp() for _ in range(30)] + [_low_detail() for _ in range(30)]
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2
    assert r["blur_frame_ratio"] > 0.0, "只看首帧会得出 0.0（漏检）"
    # 边界语义：BLUR_RATIO_LIMIT 是 <=，恰好过半（0.5）仍算通过
    assert r["blur_frame_ratio"] == pytest.approx(qc.BLUR_RATIO_LIMIT)
    assert r["passed"] is True
    assert r["flat_frame_ratio"] == 0.0, "低细节内容不该被当成空帧"


def test_majority_low_detail_no_longer_fails(patch_capture):
    """★ 2026-09-18 判定口径修正：低细节帧过半**不再**判不通过（旧契约已反转）。

    旧契约（本用例的前身）是「过半采样帧低细节 → 判不通过」。推翻它的证据是
    44 个唯一真实段里被判「低细节过半」的 4 段：逐帧抽图目视 **6/6 全部清晰**
    （夜景浅景深、柔光人脸、暗场光束特效）—— 这个指标测的是**画面高频细节量**，
    与「人眼觉得糊」是两件事，换任何细节量指标两组分布都重叠。

    指标本身仍然如实上报（`blur_frame_ratio`），只是不再否决产物。
    """
    frames = [_sharp()] * 30 + [_low_detail()] * 60
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 3
    assert r["blur_frame_ratio"] == pytest.approx(2 / 3, abs=0.01), "指标必须如实上报"
    assert r["passed"] is True, "低细节不再是失败原因（它曾让 4/44 段误报，等于狼来了）"
    assert r["failed_reasons"] == [], "失败成因里不该出现模糊/低细节"


def test_blur_ratio_is_fraction_of_sampled_frames(patch_capture):
    """采样点一半清晰一半低细节 → blur_frame_ratio == 0.5（真实比例，不是 0/1 二值）。"""
    frames = [_sharp() for _ in range(30)] + [_low_detail() for _ in range(30)]
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2
    assert r["blur_frame_ratio"] == pytest.approx(0.5), (
        f"blur 比例应为 0.5，实际 {r['blur_frame_ratio']} —— "
        f"落在 0.0/1.0 两个极端说明采样计数仍然错误"
    )


# ------------------------------------------------- 空帧（2026-09-18 新增规则）

def test_empty_frame_is_detected_and_fails_zero_tolerance(patch_capture):
    """★ 单帧纯色/全黑（无内容）必须被单独识别为**空帧**并判不通过。

    改动前的漏检形态（实测真实产物踩到过两段）：一块纯色画面里像素并不「黑」
    （不是「95% 像素 < 10」），于是 `black_frame_ratio` 报 0.00；
    而它方差 ≈ 0 只会被记进 blur 桶、还可能因为「不过半」而**整体判通过**。
    这条用例锁住「空帧零容忍」。
    """
    frames = [_sharp() for _ in range(30)] + [_flat() for _ in range(30)]
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2
    assert r["flat_frame_ratio"] == pytest.approx(0.5), "空帧必须被单独报出来"
    assert r["black_frame_ratio"] == 0.0, "纯色 128 不是黑帧（像素没低于 10）——正是漏检根源"
    # 关键：blur 恰好过半（0.5 <= LIMIT）本该放行，仅有空帧规则能拦住它
    assert r["blur_frame_ratio"] == pytest.approx(qc.BLUR_RATIO_LIMIT)
    assert r["passed"] is False, "有空帧就必须判不通过（零容忍）"


def test_report_shape_includes_flat_ratio(patch_capture):
    patch_capture([_sharp() for _ in range(30)], fps=30.0)
    r = qc.analyze_video_frames("dummy.mp4")
    assert set(r) >= {"total_frames", "declared_frames", "truncated",
                      "black_frame_ratio", "blur_frame_ratio",
                      "flat_frame_ratio", "passed", "failed_reasons"}
    assert r["flat_frame_ratio"] == 0.0
    assert r["truncated"] is False


# ------------------------------------------------- 残片（2026-09-18 新增判据）

def test_truncated_file_is_flagged_and_fails(patch_capture):
    """★ 解码提前中断 = 文件不完整，必须单独报出来并判不通过。

    实测成因（真实产物）：下载残片只有 129~190KB（正常 3~8MB），
    容器声明 107 帧、只能解出 11~12 帧 —— 此时黑帧/模糊比例是拿十几个采样点
    （甚至 1 个）算出来的，结论不可信。不报的话，它会被当成「画面全黑」，
    把用户引向「模型生成坏了」而不是「下载坏了」。
    """
    # 声明 200 帧、只给 30 帧 → decoded_ratio = 0.15
    patch_capture([_sharp() for _ in range(30)], fps=30.0, declared=200)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["declared_frames"] == 200
    assert r["truncated"] is True
    assert r["passed"] is False, "残片不能因为「画面清晰」就判通过"
    # 反面对照：同一批帧只要声明帧数一致，就不该被判残片
    patch_capture([_sharp() for _ in range(30)], fps=30.0, declared=30)
    r2 = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)
    assert r2["truncated"] is False
    assert r2["passed"] is True


def test_declared_frame_count_unknown_does_not_flag_truncation(patch_capture):
    """探测不到声明帧数（部分容器/流）时不能误判成残片 —— 探测失败不否决。"""
    patch_capture([_sharp() for _ in range(30)], fps=30.0, declared=0)
    r = qc.analyze_video_frames("dummy.mp4")
    assert r["truncated"] is False
    assert r["passed"] is True


# ------------------------------------------------- 黑帧：帧级上限（量纲修正）

def test_black_frame_limit_is_frame_level_not_pixel_ratio(patch_capture):
    """★ 黑帧的**帧级**上限必须独立于「像素比例阈值」。

    改动前 `passed` 写成 `black_ratio <= black_ratio_threshold`（0.95），
    拿帧比例去比像素比例阈值 —— 于是「一半采样帧全黑」也算通过。
    这里 10 个采样点里 5 个黑帧 = 0.5，按新规则（上限 0.2）必须不通过。
    """
    # 271 帧、fps=30 → 采样点 0/30/.../270 共 10 个
    frames = [_dark()] * 30 * 5 + [_sharp()] * 30 * 4 + [_sharp()]
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 10
    assert r["black_frame_ratio"] == pytest.approx(0.5)
    assert r["passed"] is False, "帧级黑帧上限被忽略时这里会误判为通过"


def test_single_black_frame_does_not_condemn_the_whole_shot(patch_capture):
    """反过来：偶尔一帧黑（如淡入）不该否决整段 —— 上限留了余量。"""
    frames = [_sharp()] * 30 + [_dark()] + [_sharp()] * 30 * 8
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 10
    assert r["black_frame_ratio"] == pytest.approx(0.1)
    assert r["black_frame_ratio"] <= qc.BLACK_FRAME_RATIO_LIMIT
    assert r["passed"] is True


def test_all_flat_video_is_fully_blurry(patch_capture):
    """全程纯色 → blur 与黑帧都…注意：纯色 128 不算黑帧，但空帧比例 1.0。"""
    patch_capture([_flat() for _ in range(60)], fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2
    assert r["blur_frame_ratio"] == pytest.approx(1.0)
    assert r["flat_frame_ratio"] == pytest.approx(1.0)
    assert r["passed"] is False


def test_all_sharp_video_passes(patch_capture):
    patch_capture([_sharp() for _ in range(60)], fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["blur_frame_ratio"] == pytest.approx(0.0)
    assert r["passed"] is True


# ------------------------------------------------- 失败成因（2026-09-18 新增字段）

def test_failed_reasons_lists_only_deterministic_causes(patch_capture):
    """★ `failed_reasons` 是上层的分流依据（自愈挑修正后缀 / 文案归因），
    必须只由**确定性**判据组成，且与 `passed` 严格一致。

    「低细节」永远不该出现在里面 —— 那是参考指标（见模块 docstring）。
    """
    # 空帧（纯色）→ 只有 flat_frames
    patch_capture([_sharp() for _ in range(30)] + [_flat() for _ in range(30)], fps=30.0)
    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)
    assert r["passed"] is False
    assert r["failed_reasons"] == ["flat_frames"], (
        "空帧虽然也会进 blur 桶，但它是最确定的缺陷，成因必须只报 flat_frames"
    )

    # 下载残片 → 只有 truncated
    patch_capture([_sharp() for _ in range(30)], fps=30.0, declared=200)
    r2 = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)
    assert r2["failed_reasons"] == ["truncated"]

    # 黑帧过半 → black_frames；暗噪声帧方差高，不是空帧
    patch_capture([_dark()] * 30 * 5 + [_sharp()] * 30 * 5, fps=30.0)
    r3 = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)
    assert r3["failed_reasons"] == ["black_frames"], "暗噪声帧不是空帧（方差 > 阈值）"

    # 干净产物 → 空列表
    patch_capture([_sharp() for _ in range(60)], fps=30.0)
    r4 = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)
    assert r4["passed"] is True
    assert r4["failed_reasons"] == []


def test_passed_and_failed_reasons_never_disagree(patch_capture):
    """`passed` 必须恒等于「failed_reasons 为空」—— 两者各自演化必然漂移。"""
    cases = [
        ([_sharp()] * 60, {}),
        ([_sharp()] * 30 + [_flat()] * 30, {}),
        ([_dark()] * 150 + [_sharp()] * 150, {}),
        ([_sharp()] * 30, {"declared": 200}),
        ([_low_detail()] * 60, {}),
    ]
    for frames, kw in cases:
        patch_capture(frames, fps=30.0, **kw)
        r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)
        assert r["passed"] is (not r["failed_reasons"]), (
            f"passed={r['passed']} 与 failed_reasons={r['failed_reasons']} 矛盾"
        )


def test_black_frames_are_detected(patch_capture):
    """全黑帧 → black_frame_ratio == 1.0（黑帧规则本身要能生效）。"""
    black = np.zeros((64, 64, 3), dtype=np.uint8)
    patch_capture([black for _ in range(60)], fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["black_frame_ratio"] == pytest.approx(1.0)
    assert r["passed"] is False


def test_release_is_called(patch_capture):
    cap = patch_capture([_sharp() for _ in range(30)], fps=30.0)
    qc.analyze_video_frames("dummy.mp4")
    assert cap.released is True


def test_unopenable_file_raises_file_not_found(monkeypatch):
    class _Bad:
        def isOpened(self):
            return False

    monkeypatch.setattr(qc.cv2, "VideoCapture", lambda _p: _Bad())
    with pytest.raises(FileNotFoundError):
        qc.analyze_video_frames("nope.mp4")
