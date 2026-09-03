"""OpenCV 视频 QC 工具 — 黑帧/模糊帧检测。"""
from __future__ import annotations

import numpy as np
import cv2
from typing import Optional


BLACK_RATIO_THRESHOLD = 0.95       # 全黑像素比例 > 此值视为黑帧
BLUR_VARIANCE_THRESHOLD = 50.0     # Laplacian 方差 < 此值视为模糊帧


def analyze_video_frames(
    video_url: str,
    fps_sample_interval: int = 1,
    black_ratio_threshold: float = BLACK_RATIO_THRESHOLD,
    blur_variance_threshold: float = BLUR_VARIANCE_THRESHOLD,
) -> dict:
    """分析视频帧，返回 QC 报告。

    参数：
        video_url: 本地路径或 http(s) URL（暂只支持本地路径；URL 需先下载）
        fps_sample_interval: 采样间隔，默认每秒 1 帧（fps / interval ≈ 1）
        black_ratio_threshold: 黑帧判定阈值（默认 95%）
        blur_variance_threshold: 模糊帧判定阈值（默认 50.0）

    返回：
        qc_report: {
            total_frames: int,
            black_frame_ratio: float,
            blur_frame_ratio: float,
            passed: bool,
        }
    """
    cap = cv2.VideoCapture(video_url)
    if not cap.isOpened():
        raise FileNotFoundError(f"无法打开视频文件: {video_url}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_interval = max(1, int(fps / fps_sample_interval))

    total = 0
    black_count = 0
    blur_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if total % frame_interval == 0:
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

    cap.release()

    black_ratio = black_count / total if total > 0 else 0.0
    blur_ratio = blur_count / total if total > 0 else 0.0

    # 通过条件：黑帧比例和模糊帧比例均低于阈值
    passed = black_ratio <= black_ratio_threshold and blur_ratio <= 0.5

    return {
        "total_frames": total,
        "black_frame_ratio": round(black_ratio, 4),
        "blur_frame_ratio": round(blur_ratio, 4),
        "passed": passed,
    }
