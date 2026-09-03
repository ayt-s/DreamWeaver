"""JSON 解析工具 — 容错处理 LLM 输出。

LLM 有时会输出 markdown 围栏代码块包裹 JSON，或附带额外文本。
此模块提供容错的 JSON 解析函数，确保下游代码不因格式问题崩溃。
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def extract_json(text: str) -> str:
    """从文本中提取第一个 JSON 对象/数组。

    处理情况：
    - 纯 JSON
    - markdown 代码块包裹 (```json ... ```)
    - JSON 前后有额外文本
    """
    # 尝试直接解析
    try:
        parsed = json.loads(text)
        return json.dumps(parsed)
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块提取
    code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if code_block_match:
        try:
            parsed = json.loads(code_block_match.group(1).strip())
            return json.dumps(parsed)
        except json.JSONDecodeError:
            pass

    # 尝试找到第一个 { 或 [ 到对应的结束位置
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start_idx = text.find(start_char)
        if start_idx == -1:
            continue
        depth = 0
        for i in range(start_idx, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start_idx:i + 1])
                        return json.dumps(parsed)
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"无法从文本中提取 JSON: {text[:200]}")


def parse_llm_json(text: str) -> Any:
    """解析 LLM 输出的 JSON，返回 Python 对象。"""
    try:
        cleaned = extract_json(text)
        return json.loads(cleaned)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"JSON 解析失败: {e}")
        return None
