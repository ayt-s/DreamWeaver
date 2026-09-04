"""小说分章 / 分段工具单元测试。"""
import pytest

from app.novel.splitter import split_chapters, split_paragraphs


def test_split_chapters_standard_chinese_numbering():
    text = """
第一章 山坡初遇
少年陈浔背着竹篓从山下村口走出来。

第二章 月夜村口
夜色渐浓，陈浔独自走到村口的老槐树下。
"""
    chapters = split_chapters(text)
    assert len(chapters) == 2
    assert chapters[0]["index"] == 1
    assert chapters[1]["index"] == 2
    assert "山坡初遇" in chapters[0]["title"]
    assert "月夜村口" in chapters[1]["title"]
    assert "陈浔" in chapters[0]["content"]


def test_split_chapters_arabic_digits():
    text = "第1章 序\n正文内容。\n第10章 尾声\n尾声内容。"
    chapters = split_chapters(text)
    assert len(chapters) == 2
    assert chapters[0]["title"] == "第1章 序"
    assert chapters[1]["title"] == "第10章 尾声"


def test_split_chapters_no_marker_fallback():
    """没有章节标记时，整段作为 1 章。"""
    text = "少年陈浔背着竹篓从山下村口走出来。天色正晴。"
    chapters = split_chapters(text)
    assert len(chapters) == 1
    assert chapters[0]["index"] == 1
    assert chapters[0]["content"] == text.strip()


def test_split_chapters_empty_text():
    assert split_chapters("") == []
    assert split_chapters("   \n  ") == []


def test_split_paragraphs_short_text_single():
    text = "短句一句。"
    assert split_paragraphs(text, max_len=100) == [text]


def test_split_paragraphs_long_text_splitted():
    # 造一个 800 字的文本，用句号切分
    sents = ["第" + str(i) + "句话内容内容。" for i in range(30)]
    text = "".join(sents)
    result = split_paragraphs(text, max_len=100)
    assert len(result) >= 2
    for para in result:
        assert len(para) <= 100 or para.count("。") == 1
    # 所有字符都被保留（无丢失）
    assert sum(len(p) for p in result) >= len(text) - 5  # 允许少量空格


def test_split_paragraphs_hard_split_no_punct():
    """没有标点的超长纯字符串按 max_len 硬切。"""
    text = "abcdefg" * 100  # 700 字符
    result = split_paragraphs(text, max_len=100)
    for para in result:
        assert len(para) <= 100
    assert "".join(result) == text
