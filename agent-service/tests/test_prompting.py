"""可灵式精细控制单测：运镜结构化 / 风格与负面词 / 时间轴分配 / 元素语义绑定。"""
from app.nodes.script import _distribute_duration, _normalize_shot_count
from app.utils.prompting import (
    build_cn_description,
    build_reference_bindings,
    camera_phrase,
    normalize_camera_spec,
)


# ---------- ③ 运镜结构化 ----------

def test_camera_phrase_full_spec():
    spec = {"shot_size": "远景", "angle": "俯拍", "movement": "推近"}
    assert camera_phrase(spec) == "wide shot, high-angle shot, slow push-in"


def test_camera_phrase_partial_spec():
    assert camera_phrase({"movement": "固定"}) == "static camera"


def test_camera_phrase_empty_and_invalid():
    assert camera_phrase({}) == ""
    assert camera_phrase(None) == ""
    assert camera_phrase("not-a-dict") == ""
    # 白名单外的值必须丢弃（防脏值注入提示词）
    assert camera_phrase({"shot_size": "无人机视角", "movement": "推近"}) == "slow push-in"


def test_normalize_camera_spec_filters_whitelist():
    out = normalize_camera_spec({"shot_size": "特写", "angle": "bogus", "movement": "跟拍", "extra": "x"})
    assert out == {"shot_size": "特写", "movement": "跟拍"}


# ---------- ① 风格 + 负面词 ----------

def test_build_cn_description_appends_style_and_negative():
    out = build_cn_description(["一只猫在太空漫步"], style_prompt="3D写实国漫风", negative_prompt="手指畸形")
    assert "一只猫在太空漫步" in out
    assert "画面风格：3D写实国漫风" in out
    assert "避免出现：手指畸形" in out


def test_build_cn_description_skips_blank_parts():
    out = build_cn_description(["主体", "", "  "], style_prompt="", negative_prompt="")
    assert out == "主体"


# ---------- ④ 元素语义绑定 ----------

def test_build_reference_bindings_uses_picture_index():
    roles, keeps = build_reference_bindings([
        {"name": "我", "image_index": 2},
        {"name": "破旧摩托车", "image_index": 3},
    ])
    assert roles and '"我" refers to <Picture 2>' in roles[0]
    assert '"破旧摩托车" refers to <Picture 3>' in roles[0]
    assert keeps and "我" in keeps[0] and "破旧摩托车" in keeps[0]


def test_build_reference_bindings_ignores_invalid():
    assert build_reference_bindings([]) == ([], [])
    assert build_reference_bindings(None) == ([], [])
    # image_index < 1 或缺失 → 丢弃
    assert build_reference_bindings([{"name": "x", "image_index": 0}]) == ([], [])
    assert build_reference_bindings([{"image_index": 1}]) == ([], [])


# ---------- ② 时间轴 ----------

def test_distribute_duration_exact_sum():
    script = [{"shot_id": i} for i in range(4)]
    _distribute_duration(script, 30)
    assert [s["duration"] for s in script] == [8, 8, 7, 7]
    assert sum(s["duration"] for s in script) == 30


def test_distribute_duration_even():
    script = [{"shot_id": i} for i in range(3)]
    _distribute_duration(script, 12)
    assert [s["duration"] for s in script] == [4, 4, 4]


def test_distribute_duration_empty_script():
    script: list = []
    _distribute_duration(script, 10)
    assert script == []


def test_normalize_shot_count_truncate():
    script = [{"shot_id": i} for i in range(5)]
    out = _normalize_shot_count(script, 3)
    assert len(out) == 3


def test_normalize_shot_count_pad():
    script = [{"shot_id": 0, "visual": "画面"}]
    out = _normalize_shot_count(script, 3)
    assert len(out) == 3
    assert out[-1]["shot_id"] == 2
