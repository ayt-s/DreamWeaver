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

## 阈值尚未按真实产物标定（Task A6，进行中）

`BLACK_RATIO_THRESHOLD` / `BLUR_VARIANCE_THRESHOLD` / `BLUR_RATIO_LIMIT` 三个值
仍是初始值，标定脚本见 `scripts/calibrate_qc_thresholds.py`。注意核心陷阱：

**低 Laplacian 方差 ≠ 人眼觉得模糊**。以下天然低方差但人看着没问题，
会被误判为「模糊」：大面积平坦背景（天空/雪景/纯色幕布）、浅景深/柔化光斑、
动漫/水彩/柔光风格、特写人脸。所以标定必须**分风格**看，不能只取全局分位数。
"""
from __future__ import annotations

import numpy as np
import cv2
from typing import Optional


BLACK_RATIO_THRESHOLD = 0.95       # 全黑像素比例 > 此值视为黑帧
BLUR_VARIANCE_THRESHOLD = 50.0     # Laplacian 方差 < 此值视为模糊帧
# 判「不通过」的模糊帧比例上限（0.5 = 过半采样帧模糊才算糊）。
# 原先是写死在 passed 表达式里的字面量 0.5，提成常量以便标定时一起调整。
BLUR_RATIO_LIMIT = 0.5


def analyze_video_frames(
    video_url: str,
    fps_sample_interval: int = 1,
    black_ratio_threshold: float = BLACK_RATIO_THRESHOLD,
    blur_variance_threshold: float = BLUR_VARIANCE_THRESHOLD,
) -> dict:
    """分析视频帧，返回 QC 报告。

    参数：
        video_url: 本地路径（http(s) URL 需先下载到本地，调用方负责）
        fps_sample_interval: 采样间隔，默认每秒 1 帧（frame_interval = fps / interval）
        black_ratio_threshold: 黑帧判定阈值（默认 95% 全黑像素）
        blur_variance_threshold: 模糊判定阈值（Laplacian 方差下限）

    返回：
        qc_report: {
            total_frames: int,          # **已采样**帧数（不是视频总帧数）
            black_frame_ratio: float,   # 采样帧里黑帧的占比
            blur_frame_ratio: float,    # 采样帧里模糊帧的占比
            passed: bool,               # 黑帧比例 ≤ 阈值 且 模糊比例 ≤ BLUR_RATIO_LIMIT
        }
    """
    cap = cv2.VideoCapture(video_url)
    if not cap.isOpened():
        raise FileNotFoundError(f"无法打开视频文件: {video_url}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(fps / fps_sample_interval))

    total = 0          # 已采样帧数（报告口径）
    frame_index = 0    # 原始帧序号（判定采样点用这个 —— 不能用 total，见模块 docstring）
    black_count = 0
    blur_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_index % frame_interval == 0:
            total += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # 黑帧检测：计算全黑像素比例
            black_pixels = np.count_nonzero(gray < 10)
            total_pixels = gray.size
            if black_pixels / total_pixels > black_ratio_threshold:
                black_count += 1

            # 模糊检测：Laplacian 方差
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            if laplacian_var < blur_variance_threshold:
                blur_count += 1

        frame_index += 1

    cap.release()

    black_ratio = black_count / total if total > 0 else 0.0
    blur_ratio = blur_count / total if total > 0 else 0.0

    # 通过条件：黑帧比例和模糊帧比例均低于阈值
    passed = black_ratio <= black_ratio_threshold and blur_ratio <= BLUR_RATIO_LIMIT

    return {
        "total_frames": total,
        "black_frame_ratio": round(black_ratio, 4),
        "blur_frame_ratio": round(blur_ratio, 4),
        "passed": passed,
    }
