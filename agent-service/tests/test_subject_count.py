"""候选主体计数的解析契约（2026-09-18）。

锁两件：① 模型回答的**各种真实形态**都要能抠出数字（围栏 / 同义字段 / 字符串数字）；
② 抠不出来时必须返回 `None` —— **绝不猜、不给默认值**，否则界面上会出现一个编出来的数字。
"""
from app.tools.subject_count import SUBJECT_QUESTION, parse_subject_answer, subject_label


def test_markdown_fenced():
    """实测最常见形态：```json {...} ```（13 张标定里的绝大多数）。"""
    text = '```json\n{\n  "people": 1,\n  "animals": 2,\n  "face_closeup": false\n}\n```'
    assert parse_subject_answer(text) == {"people": 1, "animals": 2, "faceCloseup": False}


def test_bare_json():
    assert parse_subject_answer('{"people": 2, "animals": 1, "face_closeup": true}') == {
        "people": 2, "animals": 1, "faceCloseup": True}


def test_json_with_surrounding_text():
    text = '我数了一下：{"people": 1, "animals": 1, "face_closeup": false} 以上。'
    assert parse_subject_answer(text) == {"people": 1, "animals": 1, "faceCloseup": False}


def test_alias_keys_and_string_numbers():
    """模型偶尔给 persons/cattle、或把数字写成字符串（含「1 人」这种带单位）。"""
    assert parse_subject_answer('{"persons": "1 人", "cattle": "2 头", "closeup": "是"}') == {
        "people": 1, "animals": 2, "faceCloseup": True}


def test_bool_is_not_a_number():
    """`{"people": true}` 是典型的「很想要一个数」——把它当 1 就是编数据。"""
    assert parse_subject_answer('{"people": true, "animals": 1}') is None


def test_missing_required_field():
    assert parse_subject_answer('{"people": 1}') is None
    assert parse_subject_answer('{"animals": 2}') is None


def test_no_json_returns_none():
    assert parse_subject_answer("") is None
    assert parse_subject_answer("我看不清这张图") is None
    assert parse_subject_answer('{"people": 1, "animals":') is None
    assert parse_subject_answer(None) is None  # type: ignore[arg-type]


def test_missing_closeup_is_not_a_failure():
    """角标的主体是两个计数；`face_closeup` 缺失不该让整条判定作废。"""
    assert parse_subject_answer('{"people": 1, "animals": 1}') == {
        "people": 1, "animals": 1, "faceCloseup": None}


def test_negative_clamped_to_zero():
    assert parse_subject_answer('{"people": -1, "animals": 1}') == {
        "people": 0, "animals": 1, "faceCloseup": None}


def test_subject_label():
    assert subject_label({"people": 1, "animals": 1}) == "1人1牛"
    assert subject_label({"people": 2, "animals": 0}) == "2人"
    assert subject_label({}) == "0人"


def test_question_contract():
    """题面必须要求 JSON 且含两个计数字段（换题面要重标，所以钉住）。"""
    assert "JSON" in SUBJECT_QUESTION
    assert "face_closeup" in SUBJECT_QUESTION
    assert "animals" in SUBJECT_QUESTION
