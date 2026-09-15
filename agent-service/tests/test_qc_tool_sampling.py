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
import numpy as np
import pytest

from app.tools import qc


class _FakeCapture:
    """假 VideoCapture：逐帧返回预设图像，模拟任意 fps / 帧数。"""

    def __init__(self, frames: list[np.ndarray], fps: float = 30.0):
        self._frames = frames
        self._i = 0
        self._fps = fps
        self.released = False

    def isOpened(self) -> bool:
        return True

    def get(self, prop):
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
    """低 Laplacian 方差：纯色（无高频细节）→ 会被判为「模糊」。"""
    return np.full((h, w, 3), 128, dtype=np.uint8)


@pytest.fixture
def patch_capture(monkeypatch):
    def _install(frames, fps=30.0):
        cap = _FakeCapture(frames, fps)
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
    """首帧清晰、第 30 帧平坦 → 结论必须反映「有模糊帧」，不能只看首帧就放行。

    修复前 `total_frames` 恒为 1，这个视频会得到 blur=0.0、passed=True（漏检）。
    """
    frames = [_sharp() for _ in range(30)] + [_flat() for _ in range(30)]
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2
    assert r["blur_frame_ratio"] > 0.0, "只看首帧会得出 0.0（漏检）"
    # 边界语义：BLUR_RATIO_LIMIT 是 <=，恰好过半（0.5）仍算通过
    assert r["blur_frame_ratio"] == pytest.approx(qc.BLUR_RATIO_LIMIT)
    assert r["passed"] is True


def test_majority_blurry_fails(patch_capture):
    """过半采样帧模糊 → 判不通过（越过边界的另一侧）。

    90 帧、fps=30 → frame_interval=30 → 采样第 0/30/60 帧（第 90 帧不存在）。
    第 0 帧清晰、第 30/60 帧平坦 → blur = 2/3 > BLUR_RATIO_LIMIT。
    """
    frames = [_sharp()] * 30 + [_flat()] * 60
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 3
    assert r["blur_frame_ratio"] == pytest.approx(2 / 3, abs=0.01)
    assert r["passed"] is False


def test_blur_ratio_is_fraction_of_sampled_frames(patch_capture):
    """采样点一半清晰一半平坦 → blur_frame_ratio == 0.5（真实比例，不是 0/1 二值）。"""
    frames = [_sharp() for _ in range(30)] + [_flat() for _ in range(30)]
    patch_capture(frames, fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2
    assert r["blur_frame_ratio"] == pytest.approx(0.5), (
        f"blur 比例应为 0.5，实际 {r['blur_frame_ratio']} —— "
        f"落在 0.0/1.0 两个极端说明采样计数仍然错误"
    )


def test_all_flat_video_is_fully_blurry(patch_capture):
    """全程平坦 → blur_frame_ratio == 1.0（这是真实结论，不是采样 bug）。"""
    patch_capture([_flat() for _ in range(60)], fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["total_frames"] == 2
    assert r["blur_frame_ratio"] == pytest.approx(1.0)


def test_all_sharp_video_passes(patch_capture):
    patch_capture([_sharp() for _ in range(60)], fps=30.0)

    r = qc.analyze_video_frames("dummy.mp4", fps_sample_interval=1)

    assert r["blur_frame_ratio"] == pytest.approx(0.0)
    assert r["passed"] is True


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
