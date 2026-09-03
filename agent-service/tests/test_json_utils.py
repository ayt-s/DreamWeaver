"""容错 JSON 解析的回归测试（2026-09 实测发现的两个真 bug 的防线）。"""
import pytest

from app.utils.json_utils import parse_llm_json


class TestParseLlmJson:
    def test_plain_json(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_markdown_fence_block(self):
        # 实测：agnes-2.5-flash 常返回 ```json 围栏包裹 —— 旧代码 json.loads 直接炸
        raw = '```json\n{"theme": "test", "style": "cool"}\n```'
        assert parse_llm_json(raw)["theme"] == "test"

    def test_surrounding_noise(self):
        # 模型在 JSON 前后加了废话
        raw = '好的，这是结果：\n[{"shot_id": 1, "visual": "apple", "duration": 4}]\n完毕'
        result = parse_llm_json(raw)
        assert result[0]["shot_id"] == 1

    def test_list_fence(self):
        raw = '```json\n[{"shot_id": 1}]\n```'
        assert parse_llm_json(raw)[0]["shot_id"] == 1

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_llm_json("   ")