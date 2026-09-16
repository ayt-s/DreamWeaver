"""分镜忠实度校验（LLM）：切出来的分镜是否忠实于小说原文。

## 为什么需要它

`qc_checker` 只做**画面层**检查（黑帧/模糊/时长/画幅），并明确声明"不做语义判分"。
于是剧情层面的偏差一路无人拦：小说里的主线被切丢、或分镜里冒出原文没有的人物事件，
用户要等到**视频生成完**（花钱 + 几分钟）才发现故事不是自己要的。实测故障：一次任务的
剧情跑成"修士渡劫被烤鸡腿砸头"，QC 仍然 4/4 通过。

本模块把这一步补上，**位置在小说预处理的 storyboarder 之后**——也就是最省钱的拦截点：
分镜一旦确认就要转入画布、开始消耗视频额度。

## 判定口径（刻意保守）

只对两类**硬问题**判不通过：

1. 主线关键事件缺失（不是压缩、合并、省略细节）
2. 关键人物/事件凭空编造（原文里没有）

以下都不算问题（正常改编）：文学修辞 → 画面描述、细节压缩、多段合并、
场景具体化（「屋内」→「石屋客厅，白日」）、补充镜头术语、次要配角与过场省略。

**拿不准就判通过**：宁可漏报，也不误报——误报会让整条预处理流水线多切一次。
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.utils.retry import with_retry

logger = logging.getLogger(__name__)

# 单次校验最多回传几条问题（够 LLM 与用户判断即可，避免刷屏）
MAX_ITEMS = 5
# 警告文案上限（与 Java error_message 的 500 字符口径一致，避免下游截断出半个字）
MAX_WARNING_CHARS = 400


class FidelityReport(BaseModel):
    """忠实度结论（结构化输出，避免 LLM 自由发挥）。"""

    passed: bool = Field(..., description="是否忠实：只有主线关键事件缺失或关键人物/事件编造才填 false")
    reason: str = Field(default="", description="一句话结论，≤60 字")
    missing: list[str] = Field(default_factory=list, description="原文有、分镜里没体现的关键事件，每条一句话，最多 5 条")
    invented: list[str] = Field(default_factory=list, description="分镜里有、原文没有的关键人物/事件，每条一句话，最多 5 条")


SYSTEM_PROMPT = """你是小说改编的忠实度审校。判断「分镜列表」是否忠实于「小说原文」。

只有两类问题判 passed=false：
1. 主线关键事件缺失：原文中推动剧情的关键事件/转折，在分镜里完全没有（注意：压缩、合并、只拍其中一段都算体现了，不算缺失）
2. 关键人物/事件凭空编造：分镜里出现了原文没有的人物，或原文没有的关键事件

以下情况**不算**不忠实（这是正常改编，必须判 passed=true）：
- 文学修辞被改写成画面描述；情节被压缩、多段合并成一段
- 场景被具体化（如「屋内」→「石屋客厅，白日，暖黄灯光」）
- 为可执行性补充的镜头术语（景别/机位/运镜）
- 次要配角、过场细节被省略

判定原则：**拿不准就判 passed=true**。误报会让整条流水线重切一次，代价高于漏报。
reason 用一句话说结论；判 true 时 missing/invented 留空数组。
"""


def _shots_text(segments: list[dict]) -> str:
    lines = []
    for i, s in enumerate(segments or [], start=1):
        title = str(s.get("title") or "").strip()
        plot = str(s.get("plot") or "").strip()
        chars = "、".join(str(c) for c in (s.get("characters") or []))
        lines.append(f"{i}. 【{title}】{plot}" + (f"（出场：{chars}）" if chars else ""))
    return "\n".join(lines)


@with_retry("LLM 忠实度校验", preset="llm")
async def check_fidelity(novel_text: str, segments: list[dict], model: Any) -> dict:
    """校验分镜是否忠实于原文，返回 {passed, reason, missing, invented}。"""
    from pydantic_ai import Agent

    agent = Agent(model=model, output_type=FidelityReport)
    prompt = (
        f"小说原文（节选）：\n{novel_text[:6000]}\n\n"
        f"分镜列表（共 {len(segments or [])} 段）：\n{_shots_text(segments)}"
    )
    result = await agent.run(prompt, instructions=SYSTEM_PROMPT)
    report: FidelityReport = result.output
    return {
        "passed": bool(report.passed),
        "reason": (report.reason or "").strip()[:200],
        "missing": [str(x).strip()[:120] for x in (report.missing or []) if str(x).strip()][:MAX_ITEMS],
        "invented": [str(x).strip()[:120] for x in (report.invented or []) if str(x).strip()][:MAX_ITEMS],
    }


def rewrite_hint(fidelity: dict) -> str:
    """把校验结论转成给分镜器的修正指令（重切一次时注入提示词）。"""
    parts = []
    if fidelity.get("missing"):
        parts.append("上一版漏掉了原文里的这些关键事件（必须补进分镜）：\n- "
                     + "\n- ".join(fidelity["missing"]))
    if fidelity.get("invented"):
        parts.append("上一版出现了原文没有的人物/事件（必须删除或改回原文内容）：\n- "
                     + "\n- ".join(fidelity["invented"]))
    if fidelity.get("reason"):
        parts.append(f"审校意见：{fidelity['reason']}")
    return "\n\n".join(parts)


def warning_text(fidelity: dict) -> str:
    """把结论转成给用户看的一句话（不通过时才有内容；失败/未跑时不产生警告）。"""
    if not isinstance(fidelity, dict) or fidelity.get("passed") is not False:
        return ""
    head = "分镜忠实度校验未通过"
    if fidelity.get("attempts", 1) > 1:
        head += "（已按审校意见重切一次，仍未通过）"
    body = (fidelity.get("reason") or "").strip().rstrip("。.")
    detail = []
    if fidelity.get("missing"):
        detail.append("疑似漏掉：" + "；".join(fidelity["missing"][:3]))
    if fidelity.get("invented"):
        detail.append("疑似编造：" + "；".join(fidelity["invented"][:3]))
    text = "：".join(x for x in (head, body) if x)
    if detail:
        text += "。" + " ".join(detail)
    text += "。建议先核对分镜再「转入画布」（转入后就会开始消耗生成额度）。"
    return text[:MAX_WARNING_CHARS]
