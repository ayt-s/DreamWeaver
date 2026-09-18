"""候选主体计数：让模型数「画面里有几个人 / 几头动物」（2026-09-18 标定通过）。

## 为什么需要它

项目已反复实测到「同一提示词形态会**随机多出主体**」（设定一头牛 → 出两头；
三张候选**全中招**）。而候选是同一 prompt 的三次独立请求，所以**靠候选挑救不了**：
三张一起错时，肉眼不看数字根本挑不出来。本地 OpenCV 只能判「面部特写」（YuNet），
数不了主体（试过，无可靠办法）。

## 标定结果（2026-09-18，13 张真实候选，逐张与我目视答案比对）

**13/13 一致**：三张「两头牛」的 `animals` 全数对 2、六张大脸特写全判 `faceCloseup=true`、
单主体图全对 1。⚠️ 样本偏窄（同一部小说、同一画风、13 张），所以结论**落成「角标提示」
而不是「自动淘汰候选」** —— 数错了也只是徽标误导，不会毁掉一张好图。

## 两个踩过的坑（改这里前必读）

1. **`max_tokens` 不能给小**：模型先吐 `reasoning_content`，给小了推理吃掉额度、
   `content` 变空串（实测 13 张里 7 张空，同样的图给足 token 重试就正常）。
2. **回答可能带 markdown 围栏或多余话**：`parse_subject_answer` 负责抠 JSON，
   抠不到就返回 `None`（**不猜、不给默认值**，否则会给用户一个假的数字）。
"""
from __future__ import annotations

import json
import re

# 题面固定、只回 JSON：这套题面在 13/13 标定里用的就是它，改题面要重标。
# 用「动物」而不是「牛」：项目里除了牛还有别的灵兽/家畜，泛化一点更稳。
SUBJECT_QUESTION = (
    "仔细看这张图，只回一个 JSON，不要解释、不要 markdown 代码块："
    '{"people": 清晰可见的完整人物数量（含背影，只要四肢或躯干可见就算）, '
    '"animals": 清晰可见的动物（牛/马/狗等）数量, '
    '"face_closeup": 画面是否被一张大脸占据（true/false）}'
)

# 模型可能用的同义字段名 —— 只做映射，不做数量推断
_PEOPLE_KEYS = ("people", "persons", "person", "humans", "figures", "人物", "人")
_ANIMAL_KEYS = ("animals", "animal", "cattle", "cows", "oxen", "cattle_count", "动物", "牛")
_CLOSEUP_KEYS = ("face_closeup", "closeup", "faceCloseup", "is_closeup", "面部特写")


def _pick(obj: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in obj:
            return obj[k]
    # 大小写/下划线差异的兜底（模型偶尔给 FaceCloseup / face closeup）
    low = {str(k).replace(" ", "_").lower(): v for k, v in obj.items()}
    for k in keys:
        kk = k.replace(" ", "_").lower()
        if kk in low:
            return low[kk]
    return None


def _to_int(value) -> int | None:
    """把模型给的 ``1`` / ``"1"`` / ``1.0`` / ``"1 人"`` 统一成 int；转不了返回 None。"""
    if isinstance(value, bool):
        return None            # True 当成 1 是典型的"很想要一个数"的错误
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value)
        if m:
            return int(m.group())
    return None


def parse_subject_answer(text: str) -> dict | None:
    """从模型回答里抠出 ``{people, animals, faceCloseup}``；抠不到返回 ``None``。

    **不猜、不补默认值**：宁可让前端不显示角标，也不能显示一个编出来的数字。
    只要有一个必需字段解析不出来就算失败（两个计数是角标的主体）。
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    # 剥 markdown 围栏（```json ... ```），再退一步只取第一个 {...}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        brace = re.search(r"\{.*\}", raw, re.S)
        candidate = brace.group(0) if brace else None
    if candidate is None:
        return None
    try:
        obj = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None

    people = _to_int(_pick(obj, _PEOPLE_KEYS))
    animals = _to_int(_pick(obj, _ANIMAL_KEYS))
    if people is None or animals is None:
        return None
    closeup = _pick(obj, _CLOSEUP_KEYS)
    if isinstance(closeup, str):
        closeup = closeup.strip().lower() in ("true", "yes", "1", "是", "true。")
    return {
        "people": max(0, people),
        "animals": max(0, animals),
        "faceCloseup": bool(closeup) if closeup is not None else None,
    }


def subject_label(verdict: dict) -> str:
    """角标文案：``1人1牛`` / ``2人0动物``（动物为 0 时不提，避免噪音）。"""
    people = int(verdict.get("people") or 0)
    animals = int(verdict.get("animals") or 0)
    label = f"{people}人"
    if animals > 0:
        label += f"{animals}牛"
    return label
