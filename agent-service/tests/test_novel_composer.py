"""composer 单元测试：红线注入、角色锁定、风格注入。"""
from app.novel.composer import compose_image_prompt, compose_video_prompt


REQUIRED_RED_LINES = [
    "严禁面部特写",
    "16:9",
    "4K",
    "主体清晰居中",
    "安全边距",
    "无字幕无水印",
]


def test_image_prompt_contains_all_red_lines():
    seg = {
        "id": "s1",
        "chapter": 1,
        "title": "山坡初遇",
        "plot": "少年陈浔背着竹篓走出村口。",
        "characters": ["陈浔"],
        "scene": "江南小山村山坡，春日午后",
        "camera": "广角长镜头缓慢横移",
        "seconds": 5,
        "mood": "平静、坚韧",
    }
    prompt = compose_image_prompt(seg, style="电影写实")
    for kw in REQUIRED_RED_LINES:
        assert kw in prompt, f"missing red line: {kw}"
    assert "广角长镜头" in prompt
    assert "电影写实" in prompt


def test_image_prompt_locks_character_features():
    seg = {
        "characters": ["陈浔", "黑牛"],
        "plot": "两人相遇。",
        "scene": "村口老槐树下",
        "camera": "特写",
    }
    analysis = {
        "characters": {
            "陈浔": "十六岁少年，瘦削，背竹篓",
            "黑牛": "中年壮汉，络腮胡，戴斗笠",
        }
    }
    prompt = compose_image_prompt(seg, style="水墨青蓝", analysis=analysis)
    assert "陈浔（十六岁少年，瘦削，背竹篓）" in prompt
    assert "黑牛（中年壮汉，络腮胡，戴斗笠）" in prompt


def test_image_prompt_without_character_card():
    seg = {"characters": ["陈浔"]}
    prompt = compose_image_prompt(seg, style="电影写实", analysis=None)
    assert "陈浔" in prompt
    # 无分析卡时不应带括号特征
    assert "（" not in prompt.split("情节")[0] or "情节" not in prompt


def test_video_prompt_has_seconds_and_all_red_lines():
    seg = {
        "id": "s1",
        "chapter": 1,
        "title": "x",
        "plot": "y",
        "characters": [],
        "scene": "z",
        "camera": "固定",
        "seconds": 8,
        "mood": "沉静",
    }
    prompt = compose_video_prompt(seg, style="电影写实")
    assert prompt.startswith("时长 8 秒")
    for kw in REQUIRED_RED_LINES:
        assert kw in prompt
