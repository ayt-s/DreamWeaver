"""OpenCV 视频 QC 工具 — 黑帧 / 模糊帧检测。

## 采样计数 bug（2026-09-15 A6 标定实测发现并修复）

原实现把「已采样帧数」当成了「原始帧序号」：

    total = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if total % frame_interval == 0:
            total += 1        # ← 只在采样时自增
            ...

`total` 从 0 开始，首帧条件成立 → total 变 1；此后 `1 % 30 != 0` 恒不成立，
**采样计数再也不自增**，于是整个循环只分析了第一帧、`total_frames` 恒为 1。

实测后果：21 个真实段里 `blur_frame_ratio` 呈「0 或 1.0」的双峰，
6 个段被判「整段 100% 模糊」—— 那其实是「第一帧模糊」。
一个 5~10 秒的视频被拿第一帧下结论，这个 bug 比阈值不准更致命。

修法：用独立的 `frame_index` 判定采样点，`total` 只在采样时自增。

## 低 Laplacian 方差 ≠ 人眼觉得糊（2026-09-18 定论）

核心陷阱：**低 Laplacian 方差 ≠ 人眼觉得模糊**。以下天然低方差但人看着没问题，
会被误判为「模糊」：大面积平坦背景（天空/雪景/纯色幕布）、浅景深/柔化光斑、
动漫/水彩/柔光风格、特写人脸。

实测（2026-09-18，逐帧看过）：夜间浅景深人像方差 11~20、柔光人脸特写 3~4、
暗场特效镜头 0/87/87，而 QC 判通过的对照段是 105~236。

早先的结论是「标定必须**按内容/风格分组**」—— 本轮做了分组标定尝试后**否掉了它**：
换任何细节量指标（分块上分位、纹理块占比）两组分布都重叠，**分组阈值也不成立**
（运行时无从知道内容属于哪一类）。定论见下面「blur 降级为参考指标」一节。
标定脚本仍在：`scripts/calibrate_qc_thresholds.py`。

## blur 降级为参考指标（2026-09-18 判定口径修正）

**`blur_frame_ratio` 不再参与 `passed`。** 它继续被计算、被返回、被日志与
`fix_looping` 的成因映射使用，但**不再否决任何产物**。

为什么：全图 Laplacian 方差测的是**画面高频细节量**，而「细节少」与「人眼觉得糊」
是两件事，且**任何**基于细节量的指标都分不开它们 —— 实测（44 个唯一真实段）：

- 判「低细节过半」的 4 段，逐帧抽图目视 **6/6 全部清晰**：夜间浅景深人像、
  柔光人脸特写、蓝色光束暗场特效。它们不是「糊」，是内容本身就低细节。
- 试过换指标（分块 Laplacian 方差的上分位数 p90 / p95、纹理块占比），
  误报段与对照段的分布**互相重叠**（误报段 p90 最高 141，对照段最低 126），
  换指标解决不了 —— 因为病因不是阈值位置，是「低细节 ≠ 糊」。

代价与补偿：失去这条判据后，`passed` 只由三条**确定性**判据决定（残片 / 黑帧 /
空帧），碰巧这三条在生产产物里是零误报的，于是「质检未通过」重新变得可信
（此前 4/44 全是误报，等于狼来了）。真·糊由候选机制与人工目视兜底。

## 黑帧 / 空帧：量纲与语义（2026-09-18 修正）

原实现有两个错位，本轮拆开（**判定口径变化，见下面常量注释**）：

1. `passed` 里写成 `black_ratio <= black_ratio_threshold` —— 拿**帧比例**去比
   **像素比例阈值**（0.95）。后果：一个 90% 采样帧全黑的视频算「通过」。
   现在帧级判定用独立的 `BLACK_FRAME_RATIO_LIMIT`。
2. 「空帧」此前无人认领：一块**纯色/纯黑画面**（方差 ≈ 0）既不是
   「95% 像素 < 10」的黑帧，又只被记进模糊桶。实测有两段视频的首帧就是这样，
   `black_frame_ratio` 报 0.00 而人眼看着是空的。现在有独立的
   `flat_frame_ratio`（方差 < `FLAT_VARIANCE_THRESHOLD`），**零容忍**：
   真实内容不会出现方差 < 1 的帧，而 3~4（柔光人脸）与它分得很开。
"""
from __future__ import annotations

import numpy as np
import cv2
from typing import Optional


# 单帧内的「黑像素」：灰度值低于此值算黑像素
BLACK_PIXEL_VALUE_MAX = 10
# **像素比例**阈值：一帧里超过此比例的像素是黑的 → 这一帧算「黑帧」
BLACK_PIXEL_RATIO_THRESHOLD = 0.95
# 兼容旧名（scripts/calibrate_qc_thresholds.py 等在用）——它一直是像素比例阈值
BLACK_RATIO_THRESHOLD = BLACK_PIXEL_RATIO_THRESHOLD
# **帧比例**上限：采样帧里黑帧占比超过它 → 整段判不通过。
# ⚠️ 此前 `passed` 拿这个位置去比 BLACK_RATIO_THRESHOLD（0.95），量纲不对：
#    于是「90% 的帧全黑」也算通过。p5 之外留了余量：开头一帧淡入黑不该否决整段。
BLACK_FRAME_RATIO_LIMIT = 0.2
# Laplacian 方差低于此值 → 这一帧算「低细节帧」。
# ⚠️ 2026-09-18 起**只进报告，不决定 passed**（见模块 docstring）：
#    这个指标测的是「画面高频细节量」，低细节内容（夜景/柔光/暗场特效）
#    会被算成本桶，而实测那 4 段人眼都清晰。
BLUR_VARIANCE_THRESHOLD = 50.0
# 低细节帧比例的**参考线**：超过它只意味着「值得人工看一眼」，
# **不再参与 passed**。保留是因为 `scripts/calibrate_qc_thresholds.py`
# 与 `fix_looping._pick_hint` 的成因映射仍以它为观察口径。
BLUR_RATIO_LIMIT = 0.5
# Laplacian 方差低于此值 → 「空帧」：纯色/纯黑，画面里没有任何内容。
# 与「低细节」严格区分：实测真实内容最低到 3~4（柔光人脸特写），
# 而真正的空帧是 0~1。**零容忍**（flat_frame_ratio 必须为 0），
# 因为真实拍摄/生成不会产出完全均匀的帧。
FLAT_VARIANCE_THRESHOLD = 1.0
# 解码帧数 / 容器声明帧数 低于此值 → 判「文件不完整（下载残片）」。
# 实测残片：129~190KB（正常 3~8MB），声明 107 帧只解出 11~12 帧。
# 不设这条的话，QC 会拿十几个采样点（甚至 1 个）当结论 —— 与模块 docstring 里
# 那个「拿第一帧下结论」的老 bug 同源，只是成因从采样计数换成了文件损坏。
TRUNCATED_DECODE_RATIO = 0.9


def analyze_video_frames(
    video_url: str,
    fps_sample_interval: int = 1,
    black_ratio_threshold: float = BLACK_PIXEL_RATIO_THRESHOLD,
    blur_variance_threshold: float = BLUR_VARIANCE_THRESHOLD,
    black_frame_ratio_limit: float = BLACK_FRAME_RATIO_LIMIT,
    flat_variance_threshold: float = FLAT_VARIANCE_THRESHOLD,
) -> dict:
    """分析视频帧，返回 QC 报告。

    参数：
        video_url: 本地路径（http(s) URL 需先下载到本地，调用方负责）
        fps_sample_interval: 采样间隔，默认每秒 1 帧（frame_interval = fps / interval）
        black_ratio_threshold: **单帧内**黑像素比例阈值（默认 95% 像素 < 10 → 这帧算黑帧）
        blur_variance_threshold: 模糊判定阈值（Laplacian 方差下限）
        black_frame_ratio_limit: **帧级**黑帧占比上限（与上面的像素比例阈值是两回事）
        flat_variance_threshold: 空帧判定阈值（方差低于它 = 纯色画面，没内容）

    返回：
        qc_report: {
            total_frames: int,          # **已采样**帧数（不是视频总帧数）
            black_frame_ratio: float,   # 采样帧里黑帧的占比
            blur_frame_ratio: float,    # 采样帧里低细节帧的占比（**参考，不决定 passed**）
            flat_frame_ratio: float,    # 采样帧里空帧（纯色/纯黑无内容）的占比
            passed: bool,               # 见下方 passed 的判据
            failed_reasons: list[str],  # passed=False 时的确定性成因
                                        #   ("truncated" / "black_frames" / "flat_frames")
        }
    """
    cap = cv2.VideoCapture(video_url)
    if not cap.isOpened():
        raise FileNotFoundError(f"无法打开视频文件: {video_url}")

    # 容器声明的总帧数：用于识别「解码提前中断 = 文件不完整（下载残片）」
    try:
        declared_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    except Exception:  # noqa: BLE001 —— 探测失败不阻断，只是失去这条判据
        declared_frames = 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(fps / fps_sample_interval))

    total = 0          # 已采样帧数（报告口径）
    frame_index = 0    # 原始帧序号（判定采样点用这个 —— 不能用 total，见模块 docstring）
    black_count = 0
    blur_count = 0
    flat_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_index % frame_interval == 0:
            total += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # 黑帧检测：这一帧里有多少像素是黑的（像素比例 → 单帧是/否黑帧）
            black_pixels = np.count_nonzero(gray < BLACK_PIXEL_VALUE_MAX)
            total_pixels = gray.size
            if black_pixels / total_pixels > black_ratio_threshold:
                black_count += 1

            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            # 空帧：方差 ≈ 0 = 纯色/纯黑，画面里没有任何内容（与「低细节」严格区分）
            if laplacian_var < flat_variance_threshold:
                flat_count += 1
            # 模糊帧：低细节（含空帧）。两者都记，因为「低细节」在结果里也可能是误报，
            # 而「空帧」是硬缺陷 —— 分开报才能让用户/标定脚本区分。
            if laplacian_var < blur_variance_threshold:
                blur_count += 1

        frame_index += 1

    cap.release()

    black_ratio = black_count / total if total > 0 else 0.0
    blur_ratio = blur_count / total if total > 0 else 0.0
    flat_ratio = flat_count / total if total > 0 else 0.0

    # 解码提前中断 = 文件不完整（下载残片）。实测残片 129~190KB（正常 3~8MB），
    # 容器声明 107 帧、只能解出 11~12 帧 —— 此时上面那些比例是拿十几个点算的，
    # 甚至可能只剩 1 个采样点，结论不可信（与「拿第一帧下结论」同源）。
    decoded_ratio = (frame_index / declared_frames) if declared_frames > 0 else 1.0
    truncated = declared_frames > 0 and decoded_ratio < TRUNCATED_DECODE_RATIO

    # 通过条件（**全部是确定性判据**，零误报；低细节 blur 自 2026-09-18 起只报告）：
    #   文件完整（不是下载残片）
    #   黑帧占比 ≤ 帧级上限（**不是**像素比例阈值 —— 这里曾把两者混用）
    #   没有空帧（零容忍：真内容不会出现方差 < 1 的帧）
    reasons: list[str] = []
    if truncated:
        reasons.append("truncated")
    if black_ratio > black_frame_ratio_limit:
        reasons.append("black_frames")
    if flat_ratio > 0.0:
        reasons.append("flat_frames")
    passed = not reasons

    return {
        "total_frames": total,
        "declared_frames": declared_frames,
        "truncated": truncated,
        "black_frame_ratio": round(black_ratio, 4),
        "blur_frame_ratio": round(blur_ratio, 4),
        "flat_frame_ratio": round(flat_ratio, 4),
        "passed": passed,
        "failed_reasons": reasons,
    }
