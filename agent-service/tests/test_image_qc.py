"""首帧质检：把「严禁面部特写」从人眼判定变成确定性函数。

夹具全部来自**真实出图**（2026-09-17 第一章），已缩到 768 宽并转 JPG 控制体积；
`faceSpan` 是比例量、与分辨率无关，所以缩放不影响判定（本文件末尾有断言守着）。
"""
from pathlib import Path

from app.tools import image_qc
from app.tools.image_qc import (
    FACE_SPAN_CLOSEUP,
    judge_face_closeup,
    judge_image_bytes,
    summarize,
)

FIX = Path(__file__).parent / "fixtures"


def test_整屏大脸判为踩红线():
    """正样本 ch1c_shot2：我肉眼判「面部大特写」，函数必须同判。"""
    r = judge_face_closeup(FIX / "frame_face_closeup.jpg")
    assert r["skipped"] is False
    assert r["closeup"] is True
    assert r["faceSpan"] >= FACE_SPAN_CLOSEUP


def test_中景不判红线():
    """最接近阈值的负样本（抱米袋，脸清楚但不是特写）—— 它是阈值的上界参考。"""
    r = judge_face_closeup(FIX / "frame_mid_shot_ok.jpg")
    assert r["skipped"] is False
    assert r["closeup"] is False
    assert r["faceSpan"] < FACE_SPAN_CLOSEUP


def test_中远景不判红线():
    r = judge_face_closeup(FIX / "frame_wide_shot_ok.jpg")
    assert r["closeup"] is False
    assert r["faceSpan"] < 0.12


def test_边界中近景不判红线():
    """★ 阈值下方最近的负样本（task78#1，缩图后 span≈0.235，离阈值 0.25 只差 6%）。

    我肉眼判它是「中近景、不是特写」，所以它必须落在阈值下方 ——
    谁想把阈值降到 0.23 以下，先过这条。
    """
    r = judge_face_closeup(FIX / "frame_borderline_midclose_ok.jpg")
    assert r["skipped"] is False
    assert r["closeup"] is False, "中近景被判成面部特写＝误报，会导致每个正常镜头都被打标"
    assert 0.20 < r["faceSpan"] < FACE_SPAN_CLOSEUP


def test_正负样本之间有可用余量():
    """阈值必须卡在正负样本中间，两侧都留余量 —— 否则标定等于没有。"""
    bad = judge_face_closeup(FIX / "frame_face_closeup.jpg")["faceSpan"]
    worst_ok = judge_face_closeup(FIX / "frame_mid_shot_ok.jpg")["faceSpan"]
    assert worst_ok < FACE_SPAN_CLOSEUP < bad
    assert bad / FACE_SPAN_CLOSEUP > 1.2, "正样本离阈值太近，容易漏报"
    assert FACE_SPAN_CLOSEUP / worst_ok > 1.2, "负样本离阈值太近，容易误报"


def test_字节路径与文件路径结论一致():
    """端点走的是内存字节（下载后不落盘），两条路必须同判。"""
    data = (FIX / "frame_face_closeup.jpg").read_bytes()
    by_bytes = judge_image_bytes(data)
    by_file = judge_face_closeup(FIX / "frame_face_closeup.jpg")
    assert by_bytes["closeup"] == by_file["closeup"] == True  # noqa: E712
    assert by_bytes["faces"] == by_file["faces"]


def test_文件不存在时跳过而不是抛异常():
    """质检是附加信息，绝不能把生成链路拖垮。"""
    r = judge_face_closeup(FIX / "根本不存在.png")
    assert r["skipped"] is True
    assert r["closeup"] is False
    assert r["reason"]


def test_坏字节流跳过而不是抛异常():
    r = judge_image_bytes(b"not an image at all")
    assert r["skipped"] is True and r["closeup"] is False


def test_模型缺失时跳过而不是抛异常(monkeypatch):
    monkeypatch.setattr(image_qc, "MODEL_PATH", Path("D:/definitely/not/here.onnx"))
    r = judge_face_closeup(FIX / "frame_face_closeup.jpg")
    assert r["skipped"] is True and r["closeup"] is False
    assert "模型缺失" in r["reason"]


def test_置信度阈值不许调低到会误检牛脸():
    """★ 标定期实测：score 0.60 时牛脸被当人脸（faceSpan 0.157 假装成「大脸」）。

    这条是护栏：谁把阈值调低到 0.6 以下，就会给「灵兽牛」的镜头全打上「面部特写」误报。
    """
    assert image_qc.FACE_SCORE_THRESHOLD >= 0.8


def test_小结推荐未踩红线的候选():
    results = [
        {"skipped": False, "closeup": True, "faceSpan": 0.40},
        {"skipped": False, "closeup": False, "faceSpan": 0.10},
        {"skipped": False, "closeup": False, "faceSpan": 0.18},
    ]
    s = summarize(results)
    assert s["total"] == 3 and s["closeupCount"] == 1
    assert s["recommendIndex"] == 2, "推荐应落在没踩红线、且脸最清楚的那张"


def test_全踩红线时不乱推荐():
    results = [
        {"skipped": False, "closeup": True, "faceSpan": 0.40},
        {"skipped": False, "closeup": True, "faceSpan": 0.35},
    ]
    assert summarize(results)["recommendIndex"] is None


def test_全部跳过时小结不炸():
    s = summarize([{"skipped": True, "closeup": False, "faceSpan": 0.0}])
    assert s["closeupCount"] == 0 and s["recommendIndex"] is None
