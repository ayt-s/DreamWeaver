"""首帧图质检 —— 「面部特写」判定（本地 YuNet 人脸检测，**无网络、无 LLM**）。

## 为什么需要它

提示词末尾有红线「严禁面部特写」，但模型不保证服从：2026-09-17 实测把「特写」降档成
「中景」之后**仍有 1/3 出整屏大脸**（对照图 `ch1d_face_ab.png`）。此前只能靠人下载图、
拼对照图、用眼睛判 —— 不可回归、不可拦截。这里把那个判断变成确定性函数。

## 指标与阈值怎么来的（2026-09-17 用真实出图标定，含真接口回放）

用 `FaceDetectorYN`（YuNet）取最大人脸框，算
`faceSpan = max(框宽/画面宽, 框高/画面高)`。

模型文件：`app/assets/face_detection_yunet_2023mar.onnx`（232KB，来自 opencv_zoo 的
`face_detection_yunet`，随代码入库 —— **放在 `app/assets/` 而不是 `data/`**：
后者在 `.gitignore` 里、是运行时目录，放那儿会让测试在干净 clone 上跑不了）。
需要更新时：`curl -sL -o app/assets/face_detection_yunet_2023mar.onnx \
https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx`

- 正样本（整屏大脸）：**0.336~0.369** —— `POST /v1/qc/images` 对 task 72 / task 77
  各 3 张候选**全部判为 closeup=True**（这两批是我肉眼认定的「特写推近」，真接口回放对齐 ✓）
- 负样本：多数 **0.05~0.12**；最接近阈值的两张是 0.184（抱米袋）与 **0.2353**（中近景），
  目视核对「都不是特写」→ 阈值 **0.25** 卡在两者之间
- 8 个真实任务共 24 张候选回放：只有 task 72 / 77 命中，其余全不命中 ✓

⚠️ **库里的坑（标定时实测）**：本项目画的是**灵兽牛**，低置信度下 YuNet 会把**牛脸当人脸**
（`ch1_shot1_img0` 在 score≥0.60 时测出 faceSpan 0.157，看起来像大脸；把置信度提到 **0.85**
后该误检消失、同一张图降到 0.074）。所以 `FACE_SCORE_THRESHOLD` 不能调低。

⚠️ **只报不拦**：样本仍是「正样本 6 张（task 72/77）× 负样本 ~20 张」的量级，
够用来打标提示，**不够用来自动淘汰候选**。要自动筛选得先攒够误报/漏报统计。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "face_detection_yunet_2023mar.onnx"

# 人脸检测置信度下限。**别调低**：低置信度会把牛/兽的脸误判成人脸（见模块 docstring）。
FACE_SCORE_THRESHOLD = 0.85
# faceSpan 超过它判「面部特写」（踩红线）。
FACE_SPAN_CLOSEUP = 0.25
# 判定前把长边缩到这个尺寸以内：比例与分辨率无关，缩小只是提速。
_MAX_SIDE = 1024


def _skipped(reason: str) -> dict:
    """质检失败/跳过时的返回值 —— 形状与正常结果一致，**调用方不必分支**。"""
    return {
        "skipped": True,
        "reason": reason,
        "closeup": False,
        "faces": 0,
        "faceSpan": 0.0,
    }


def judge_face_array(img: np.ndarray) -> dict:
    """对一帧图像判定「是否面部特写」。

    返回 `{skipped, reason, closeup, faces, faceSpan, faceWidthRatio, faceHeightRatio}`。
    **永不抛异常**：质检是附加信息，不该让生成链路失败。
    """
    try:
        if not MODEL_PATH.exists():
            return _skipped(f"人脸检测模型缺失：{MODEL_PATH}")

        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return _skipped("图像尺寸非法")

        scale = min(1.0, _MAX_SIDE / max(h, w))
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]

        detector = cv2.FaceDetectorYN.create(
            str(MODEL_PATH), "", (w, h), score_threshold=FACE_SCORE_THRESHOLD
        )
        detector.setInputSize((w, h))
        _, out = detector.detect(img)
        if out is None or len(out) == 0:
            return {"skipped": False, "reason": "", "closeup": False, "faces": 0,
                    "faceSpan": 0.0, "faceWidthRatio": 0.0, "faceHeightRatio": 0.0}

        widths = [float(f[2]) for f in out]
        heights = [float(f[3]) for f in out]
        face_w = max(widths) / w
        face_h = max(heights) / h
        span = max(face_w, face_h)
        return {
            "skipped": False,
            "reason": "",
            "closeup": span >= FACE_SPAN_CLOSEUP,
            "faces": int(len(out)),
            "faceSpan": round(span, 4),
            "faceWidthRatio": round(face_w, 4),
            "faceHeightRatio": round(face_h, 4),
        }
    except Exception as exc:  # noqa: BLE001 —— 质检绝不能拖垮生成
        logger.warning("图像质检失败（不影响生成）：%s", exc)
        return _skipped(f"质检异常：{type(exc).__name__}")


def judge_face_closeup(image_path: str | Path) -> dict:
    """读本地图片文件判定「是否面部特写」。路径不存在/读不了 → skipped，不抛异常。"""
    try:
        img = cv2.imread(str(image_path))
    except Exception as exc:  # noqa: BLE001
        return _skipped(f"图片读取异常：{type(exc).__name__}")
    if img is None:
        return _skipped("图片读取失败（路径不存在或不是图片）")
    return judge_face_array(img)


def judge_image_bytes(data: bytes) -> dict:
    """对内存中的图片字节判定（质检端点下载后走这条，免落盘）。"""
    try:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as exc:  # noqa: BLE001
        return _skipped(f"图片解码异常：{type(exc).__name__}")
    if img is None:
        return _skipped("图片解码失败")
    return judge_face_array(img)


def summarize(results: list[dict[str, Any]]) -> dict:
    """给一组候选图做小结：有几张踩红线、推荐哪一张（未踩红线的里 faceSpan 最大的那张）。"""
    usable = [r for r in results if not r.get("skipped")]
    bad = [r for r in usable if r.get("closeup")]
    ok = [r for r in usable if not r.get("closeup")]
    best = max(ok, key=lambda r: r.get("faceSpan", 0.0)) if ok else None
    return {
        "total": len(results),
        "closeupCount": len(bad),
        "recommendIndex": results.index(best) if best is not None else None,
    }
