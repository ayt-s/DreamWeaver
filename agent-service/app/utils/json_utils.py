"""JSON 解析工具 — 容错处理 LLM 输出。

LLM 有时会输出 markdown 围栏代码块包裹 JSON，或附带额外文本。
此模块提供容错的 JSON 解析函数，确保下游代码不因格式问题崩溃。
"""
import json
import re
from typing import Any


def extract_json(text: str) -> str:
    """从文本中提取第一个 JSON 对象/数组。

    处理情况：
    - 纯 JSON
    - markdown 代码块包裹 (```json ... ```)
    - JSON 前后有额外文本
    - 数组被额外文本包裹（提取最外层结构，不误抓内层对象）
    """
    if not text or not text.strip():
        raise ValueError("空文本无法提取 JSON")

    # 1. 尝试直接解析
    try:
        parsed = json.loads(text)
        return json.dumps(parsed)
    except json.JSONDecodeError:
        pass

    # 2. markdown 代码块提取
    code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_block_match:
        try:
            parsed = json.loads(code_block_match.group(1).strip())
            return json.dumps(parsed)
        except json.JSONDecodeError:
            pass

    # 3. 括号配对提取：找第一个出现的 { 或 [（取更靠前者），按配对标到闭合
    candidates = [
        (text.find("{"), "{", "}"),
        (text.find("["), "[", "]"),
    ]
    candidates = [c for c in candidates if c[0] != -1]
    if not candidates:
        raise ValueError(f"文本中无 JSON 结构: {text[:200]}")

    start_idx, start_char, end_char = min(candidates, key=lambda c: c[0])
    depth = 0
    for i in range(start_idx, len(text)):
        if text[i] == start_char:
            depth += 1
        elif text[i] == end_char:
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start_idx : i + 1])
                    return json.dumps(parsed)
                except json.JSONDecodeError:
                    raise ValueError(f"括号配对但内容非合法 JSON: {text[start_idx:i+1][:200]}")

    raise ValueError(f"JSON 结构未闭合: {text[:200]}")


def parse_llm_json(text: str) -> Any:
    """解析 LLM 输出的 JSON，返回 Python 对象。

    失败时抛 ValueError——调用方显式处理（重试/回流），
    而非静默返回 None 让下游误判「空结果」。"""
    cleaned = extract_json(text)
    return json.loads(cleaned)