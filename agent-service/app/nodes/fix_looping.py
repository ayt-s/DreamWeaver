"""fix_looping 节点（B1）：镜级自愈——只重生质检未通过的镜。

**设计要点**

1. **只动失败镜**：通过的镜保留 `existing_video_url`（`video.py` 的复用分支会跳过它们，
   不重复烧 agnes 额度）。这是「自愈」与「全量重生」的本质区别。
2. **中止检查**：与 `video.py` 一致调 `abort.is_aborted()`。用户删任务/全量重生后
   Java 的心跳会回 `tracked=false` → agent 置中止位；不检查就会继续自动烧钱
   （与已修的 P1-3 同类问题）。
3. **修正后缀按失败原因映射到「可执行的具体指令」**，不堆通用形容词 ——
   通用形容词对扩散/视频模型的因果性弱，而「黑帧过半」这种具体成因对应的指令
   （well-lit scene / no dark frames）才有作用。成因取自结构化的 `failed_reasons`，
   **已不再把「低细节」当成因**（它 2026-09-18 退出了 `passed`；详见 `_pick_hint`）。
   策略可配（`settings.fix_hint_mode`）：
   - `off`：不带后缀，原样重生
   - `mechanism`：总是带按原因映射的后缀
   - `random50`（默认）：50/50 随机 —— 这是 B0 阶段 3 的在线 A/B，
     零额外成本地从真实流量里得到「后缀有没有用」的答案；
     `fix_history[*].used_hint` 记录本轮到哪一组
4. **不改 `video_urls`**：失败镜的 url 由 `video.py` 重新生成时按索引覆盖。
   这里只清「复用字段」**并打上 `regenerate` 标记** —— 只清复用字段不够：
   `video.py` 用 `done = len(video_urls)` 做断点恢复跳过，重生轮里 `video_urls`
   仍是满的，失败镜会被永远跳过（实测：修复轮 0 次提交，纯空转）。
5. **闸门集中判定**：`decide_repair()` 是唯一实现，`graph._fix_route` 只读结论。
   两处各写一份必然漂移，后果是无限循环或幽灵轮次（见 decide_repair 文档）。
"""
import logging
import math
import random
import time

from app.config import settings
from app.state import CreativeSessionState, TaskStatus

logger = logging.getLogger(__name__)

# 按失败原因映射的修正指令（英文，直接拼在 prompt_en 后面）
# ⚠️ 曾经有一条 `_HINT_BLUR`（slow steady camera / sharp focus）。2026-09-18 删除：
#    `blur` 已退出 `passed`，它不再是任何一次失败的成因，留着只会把「空帧」
#    失败镜（flat ⊂ blur，见 _pick_hint）引到文不对题的画质指令上。
_HINT_BLACK = ", well-lit scene, bright daylight, even exposure, no dark or black frames"
_HINT_DURATION = ", steady continuous motion, no abrupt cuts"
_HINT_ASPECT = ", keep the original aspect ratio and framing"

# 通用形容词后缀 —— 仅用于离线 A/B 的对照组（B0 阶段 2），不在随机分配里使用
HINT_GENERIC = ", sharp focus, high detail"


def _pick_hint(shot: dict, entry: dict) -> str:
    """按该镜的 QC 结论选修正后缀；无明确成因时返回空串（原样重生）。

    **成因一律读结构化的 `failed_reasons`**（`tools/qc.py` 给出），不猜中文文案。

    ⚠️ 别拿 `blur_frame_ratio` 当成因（2026-09-18 修）：`blur` 已退出 `passed`
    （它测的是画面高频细节量，低细节 ≠ 糊）。它一旦还能决定后缀，「空帧」失败镜
    就**必然**被贴上文不对题的画质指令 —— 因为空帧阈值（方差 < 1.0）严格包含在
    低细节阈值（方差 < 50.0）里，凡是空帧必然 `blur > 0.5`。
    给一个纯色/空帧缺陷加「slow steady camera / sharp focus」既改不到病灶，
    又会污染 `fix_history.used_hint` 那场「后缀有没有用」的在线 A/B（归错因）。
    """
    err = str(entry.get("error") or "")

    # 画幅 / 时长这类结构性偏差优先（它们不是画质问题，加画质词没用）
    if "画幅" in err:
        return _HINT_ASPECT
    if "时长" in err:
        return _HINT_DURATION

    reasons = entry.get("failed_reasons")
    if isinstance(reasons, (list, tuple)) and reasons:
        if "black_frames" in reasons:
            return _HINT_BLACK
        # truncated（解码提前中断 = 文件不完整/下载残片）与 flat_frames（空帧）：
        # 病灶不在提示词，加画质词只会把本来正常的运镜/构图改坏 → 原样重生。
        return ""

    # 兼容旧形状报告（无 `failed_reasons`：历史数据 / 第三方 stub）：只按黑帧比例回推。
    # 不再用 blur 回推 —— 旧报告里「blur 单独成因」的那批正是被判为误报的那批。
    if float(entry.get("black_frame_ratio") or 0.0) > 0.5:
        return _HINT_BLACK
    # 产物缺失 / 文件不存在 / 探测异常：不知道成因，原样重生
    return ""


def _decide_hint_mode() -> str:
    mode = str(getattr(settings, "fix_hint_mode", "random50") or "random50").strip().lower()
    if mode not in ("off", "mechanism", "random50"):
        logger.warning("fix_hint_mode=%s 非法，回退 random50", mode)
        return "random50"
    return mode


def decide_repair(state: dict) -> tuple[bool, str]:
    """能否再修一轮。返回 `(allowed, reason_if_not)`。

    **判定逻辑只在这里实现一份。** `graph._fix_route` 只读本函数写进 state 的结论，
    不做二次判断 —— 两处各写一份必然漂移，而漂移的后果很重：

    - 节点空转（不改 storyboard）+ 路由仍 retry → **无限循环**
      （实测 `GraphRecursionError: Recursion limit of 10007`）
    - 节点改了 storyboard 才被路由否决 → **幽灵轮次**：多余的 `fix_hint` 留在
      storyboard 里，经 notify_final 污染 Java 的 `segments_json`（段重生基线）

    四道闸门：
    1. **已中止**：用户删任务 / 全量重生换了 session_id，心跳回 `tracked=false`
       → 必须停，否则每轮重生都是白烧的 agnes 额度
    2. **没有失败镜**（QC 通过）：不该走到这里，安全收尾
    3. **轮次用尽**：`fix_round >= max_fix_rounds`
    4. **成本闸门**：失败镜数 > ceil(镜数/2) → 放弃。
       修一半和修全部一样贵（每轮按镜计费），但收益差很多；已拍板不调低此阈值。
    """
    if state.get("fix_aborted"):
        return False, "会话已中止（任务被删除或重新生成）"

    failed = (state.get("qc_report") or {}).get("failed_shots") or []
    if not failed:
        return False, "没有未通过质检的镜"

    max_rounds = int(state.get("max_fix_rounds") or 3)
    if int(state.get("fix_round") or 0) >= max_rounds:
        return False, f"自动修复已达轮次上限（{max_rounds} 轮）"

    # ⚠️ 镜数只能从 storyboard 数，**不能用 state["shot_count"]**：
    #    那是「用户请求参数」，用户没填时缺失 → 闸门静默失效 → 无限烧钱
    shots = len(state.get("storyboard") or [])
    if shots and len(failed) > math.ceil(shots / 2):
        return False, (f"未通过镜次过多（{len(failed)}/{shots} 超过半数），"
                       f"整条片重做更划算，已跳过自动修复")

    return True, ""


async def fix_looping_node(state: CreativeSessionState) -> dict:
    from app import abort, events

    session_id = state["session_id"]
    await events.emit(session_id, "node_entered",
                      {"node_id": "fix_looping", "node_name": "修复重试"})

    storyboard = state.get("storyboard") or []
    qc_report = state.get("qc_report") or {}
    failed = [int(i) for i in (qc_report.get("failed_shots") or [])]

    # 中止检查：用户已删任务 / 已全量重生（换了 session_id）→ 立刻停，
    # 否则每一轮重生都是一次白烧的 agnes 调用（回调也会被 Java 按终态丢弃）
    if abort.is_aborted(session_id):
        logger.warning("会话 %s 已中止，fix_looping 不再重生（放弃修复）", session_id)
        await events.emit(session_id, "node_completed",
                          {"node_id": "fix_looping", "summary": "会话已中止，放弃修复"})
        return {
            "fix_aborted": True,
            "fix_round": int(state.get("fix_round") or 0),
            "status": TaskStatus.FIX_LOOPING,
        }

    # **所有闸门在这里一次判定**（decide_repair 是唯一实现），判定不过就
    # 一个字节都不动 storyboard —— 否则会产生「幽灵轮次」，多余的 fix_hint
    # 会随 segments_json 污染 Java 侧的段重生基线。
    if not failed:
        logger.warning("会话 %s fix_looping 被调用但没有失败镜", session_id)

    allowed, reason = decide_repair(state)
    if not allowed:
        if failed:
            logger.info("会话 %s 放弃自动修复：%s", session_id, reason)
            await events.emit(session_id, "node_completed",
                              {"node_id": "fix_looping", "summary": f"放弃修复：{reason}"})
        return {
            "fix_give_up": True,
            "fix_give_up_reason": reason,
            "status": TaskStatus.FIX_LOOPING,
        }

    # 本轮是否使用修正后缀
    mode = _decide_hint_mode()
    if mode == "off":
        use_hint = False
    elif mode == "mechanism":
        use_hint = True
    else:  # random50 —— B0 阶段 3 的在线 A/B
        use_hint = random.random() < 0.5

    shots_by_index = {
        s.get("index"): s for s in (qc_report.get("shots") or []) if isinstance(s, dict)
    }

    new_storyboard = []
    hints_applied: dict[str, str] = {}
    for idx, shot in enumerate(storyboard):
        if not isinstance(shot, dict):
            new_storyboard.append(shot)
            continue
        if idx not in failed:
            # 通过的镜：**保留**复用字段，不重新生成（省额度）。
            # 同时**必须清掉上一轮留下的 regenerate 标记** —— 否则 video.py 会把它
            # 当成待重生镜，把已经修好的镜再烧一遍额度并覆盖掉合格产物。
            if shot.get("regenerate"):
                kept = dict(shot)
                kept.pop("regenerate", None)
                kept.pop("fix_hint", None)
                new_storyboard.append(kept)
            else:
                new_storyboard.append(shot)
            continue

        fixed = dict(shot)
        entry = shots_by_index.get(idx) or {}
        hint = _pick_hint(fixed, entry) if use_hint else ""

        # 清复用字段 → video.py 的 existing_video_url 分支失效 → 真正重新提交
        fixed.pop("existing_video_url", None)
        fixed.pop("existing_image_url", None)
        fixed.pop("pending_video_id", None)
        # ⚠️ 光清复用字段不够：video.py 用 `done = len(video_urls)` 做断点恢复跳过，
        #    重生轮里 video_urls 仍是满的 → 光靠 existing_video_url 判断会被跳过，
        #    失败镜根本不会重新提交（实测：修复轮 0 次提交，纯空转）。
        #    所以给一个**显式标记**让 video.py 越过断点恢复的跳过逻辑。
        fixed["regenerate"] = True

        # ⚠️ 修正后缀写到独立字段 `fix_hint`，**绝不改 `prompt_en`**：
        #   - 每一轮会叠加，prompt 会持续膨胀漂移
        #   - 更要紧的是 `storyboard` 会被 notify_final 当作 segments_json 交给 Java，
        #     而 segments_json 是「按段重生」的输入基线 —— 污染它等于污染重生基线
        #   （video.py 在提交时才把 prompt_en + fix_hint 拼起来）
        if hint:
            fixed["fix_hint"] = hint
        else:
            fixed.pop("fix_hint", None)
        if hint:
            hints_applied[str(idx)] = hint
        new_storyboard.append(fixed)

    new_round = int(state.get("fix_round") or 0) + 1
    history = list(state.get("fix_history") or [])
    history.append({
        "round": new_round,
        "failed_shots": sorted(failed),
        "hint_mode": mode,
        "used_hint": use_hint,
        "hints": hints_applied,
        "at": int(time.time()),
    })

    logger.info("fix_looping: 第 %d 轮，重生 %d/%d 镜（mode=%s used_hint=%s）",
                new_round, len(failed), len(storyboard), mode, use_hint)
    await events.emit(session_id, "node_completed", {
        "node_id": "fix_looping",
        "summary": f"第 {new_round} 轮修复：重生 {len(failed)} 镜"
                   + ("（带修正提示）" if use_hint else "（原样重生）"),
    })

    return {
        "storyboard": new_storyboard,
        "fix_round": new_round,
        "fix_history": history,
        "fix_aborted": False,
        # 显式清掉放弃标记：路由只读这个字段，残留会让下一轮直接 give_up
        "fix_give_up": False,
        "status": TaskStatus.FIX_LOOPING,
    }
