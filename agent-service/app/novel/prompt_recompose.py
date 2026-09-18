"""存量画布提示词重算（按**当前** composer 规则原地重写）。

## 为什么需要

画布里的提示词是**预处理时**合成的（`app/novel/composer.py` 那一刻的规则）。
改了 composer（角色锚裁剪、动物移出角色锚、镜头段清理…）**不会**自动更新存量画布 ——
要验证规则改动，此前只能手动跑技能目录里的脚本 `recompose_canvas_prompts.py`。

本模块把那套规则正式化为**纯函数**（不碰 DB、不调 Java、不写任何东西），
由 `POST /v1/novel/recompose-prompts` 暴露给前端画布页：
前端先 dry-run 展示「将变 N 条 + 每条改了什么」，用户确认后再走既有乐观锁 PUT 落库。

## 单一出处

动物判定 / 角色别名 / 镜头清理**全部 import 真实 composer 函数**，
绝不在前端或本模块里复制一份实现 —— 否则「重算」出来的形状与预处理出来的不一致，
这个入口本身就失去意义。

## 安全约束（都在实测里踩过）

- 结构不对的节点**跳过不硬改**：`prompt.split("；")` 后
  `parts[0]` 必须以 `[角色锚]` 开头、`len(parts) >= 3`、`parts[2]` 必须以 `[场景]` 开头；
- 其余字节原样保留（`[风格]`、红线等段落一个字都不动，即使它们本身也含 `；`）；
- 只动 `[角色锚]` / `[场景]` 两块，可选顺带重算 `[镜头]`。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.novel.composer import (  # noqa: F401  (单一出处：真实 composer 规则)
    _animal_names_from_analysis,
    _char_aliases,
    _is_animal,
    _sanitize_camera,
)

# 结构闸门的失败文案（前端直接展示给用户，所以写成中文且说明「为什么跳过」）
SKIP_REASON = "结构不是 [角色锚]；…；[场景] 三段式，跳过不硬改"

# 场景里追加动物时，描述取多长（与脚本一致：首个分句前 30 字）
_BRIEF_MAX = 30


def _as_analysis_dict(analysis: Any) -> dict | None:
    """请求里的 analysis 可能是 dict / JSON 字符串 / null。拿不到就返回 None（退回关键词表）。"""
    if isinstance(analysis, dict):
        return analysis
    if isinstance(analysis, str) and analysis.strip():
        try:
            parsed = json.loads(analysis)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _entries_of_anchor(part: str) -> list[tuple[str, str]]:
    """`[角色锚]` 段 → [(名字, 角色卡)]。角色卡是名字后面括号里的内容（可空）。

    ⚠️ **括号里的 `、` 会把一张角色卡切成几段**（实测画布 39 v6：
    「大黑牛（成年玄色水牛体型，…，左角齐根断去、断面粗糙泛白，…，能以后腿坐立、前蹄收起，…）」
    有两个 `、` 在卡里 → 被切成 **3** 段）。所以按**括号平衡**合并：
    只要前面还有没闭合的 `（`，后续片段就原样并回上一项 ——
    否则重算后 `[角色锚]` 会残留悬空碎片（实测就是这么坏的：
    `[角色锚] 陈浔（…）、断面粗糙泛白，右角完整呈弯月状…）`）。
    composer 侧是 `"、".join(f"{名字}（{卡}）")` 拼的，所以合回去等于还原它当初拼的那一项。
    """
    raw_items = [e.strip() for e in part.replace("[角色锚]", "").split("、") if e.strip()]
    merged: list[str] = []
    depth = 0  # 未闭合的 `（` 数量
    for e in raw_items:
        if merged and depth > 0:
            merged[-1] = f"{merged[-1]}、{e}"
        else:
            merged.append(e)
        depth += e.count("（") - e.count("）")

    out: list[tuple[str, str]] = []
    for e in merged:
        nm = re.sub(r"（.*$", "", e).strip()
        card = e[len(nm):].strip("（）") if "（" in e else ""
        out.append((nm, card))
    return out


def recompose_prompt(
    prompt: str,
    declared: set[str] | None,
    *,
    recompute_camera: bool = True,
) -> dict:
    """按当前规则重写**一条**提示词。

    `declared` 来自 `_animal_names_from_analysis`：`None` = 分析结果里没有该字段（老数据，
    退回 `_is_animal` 关键词猜）；空集 = 分析器明确说了没有非人角色（不猜）。

    返回 `{prompt, changed, skipped, reasons}`：
    - `prompt` 永远是「重算后应当写进画布」的那一条（跳过时**逐字等于入参**）；
    - `skipped=True` 表示结构不对、没动它（`changed` 必为 False）。
    """
    text = prompt or ""
    parts = text.split("；")
    if (
        not parts[0].startswith("[角色锚]")
        or len(parts) < 3
        or not parts[2].startswith("[场景]")
    ):
        return {"prompt": text, "changed": False, "skipped": True, "reasons": [SKIP_REASON]}

    entries = _entries_of_anchor(parts[0])
    keep: list[tuple[str, str]] = []
    moved: list[tuple[str, str]] = []
    for nm, card in entries:
        is_animal = nm in declared if declared is not None else _is_animal(nm, card)
        (moved if is_animal else keep).append((nm, card))

    new_anchor = "[角色锚] " + (
        "、".join(f"{nm}（{card}）" if card else nm for nm, card in keep)
        if keep
        else "无具体人物"
    )
    scene = parts[2].replace("[场景]", "").strip()
    extra: list[str] = []
    scene_already: list[str] = []
    for nm, card in moved:
        # 场景里已经点名了就不再追加（避免同一条里说两遍，agnes 是照单执行的）
        if any(a and a in scene for a in _char_aliases(nm)):
            scene_already.append(nm)
            continue
        brief = (card or "").split("，")[0].split("。")[0].strip()[:_BRIEF_MAX]
        extra.append(f"{nm}（{brief}）" if brief else nm)
    new_scene = "[场景] " + (f"{scene}，{'、'.join(extra)}" if extra else scene)

    new_parts = list(parts)
    new_parts[0], new_parts[2] = new_anchor, new_scene
    camera_touched = False
    if recompute_camera and len(new_parts) > 3 and new_parts[3].startswith("[镜头]"):
        cam = new_parts[3].replace("[镜头]", "").strip()
        new_parts[3] = f"[镜头] {_sanitize_camera(cam, bool(keep))}"
        camera_touched = new_parts[3] != parts[3]

    new_prompt = "；".join(new_parts)
    changed = new_prompt != text
    reasons: list[str] = []
    if moved:
        reasons.append(f"[角色锚] 移出：{'、'.join(nm for nm, _ in moved)}（改到 [场景] 里带出）")
    if extra:
        reasons.append(f"[场景] 追加：{'、'.join(extra)}")
    if scene_already:
        reasons.append(f"[场景] 已点名 {'、'.join(scene_already)}，不重复追加")
    if entries and len(keep) != len(entries):
        reasons.append(f"[角色锚] 从 {len(entries)} 项收窄到 {len(keep)} 项")
    if camera_touched:
        reasons.append("[镜头] 顺带按当前规则重算（删主体人数措辞 / 特写降档）")
    return {"prompt": new_prompt, "changed": changed, "skipped": False, "reasons": reasons}


def recompose_prompts(
    analysis: Any = None,
    nodes: list[dict] | None = None,
    *,
    recompute_camera: bool = True,
) -> dict:
    """批量重算：`nodes` = `[{id, prompt}]`（其余字段不看、也不回传）。

    返回 `{nodes: [{id, prompt, changed, skipped, reasons}], changed_count, animal_source}`。
    **纯函数**：不读 DB、不写画布、不发网络请求。
    """
    a = _as_analysis_dict(analysis)
    declared = _animal_names_from_analysis(a)
    animal_source = "analyzer 结构化字段" if declared is not None else "关键词表（未携带分析结果）"

    out: list[dict] = []
    changed_count = 0
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        r = recompose_prompt(
            str(n.get("prompt") or ""), declared, recompute_camera=recompute_camera
        )
        if r["changed"]:
            changed_count += 1
        out.append({"id": str(n.get("id") or ""), **r})

    return {
        "nodes": out,
        "changed_count": changed_count,
        "animal_source": animal_source,
        "recompute_camera": recompute_camera,
    }
