"""QC 阈值离线标定：用真实产物定黑帧阈值的分位数依据（低细节只作观察）。

## ⚠️ 2026-09-18 后的定位变化（先读这段，再读下面的「为什么不能跳过」）

`blur` 已**退出 `passed`**：判定只剩三条确定性判据（下载残片 / 黑帧过半 / 空帧，
见 `app/tools/qc.py` 的 `failed_reasons`）。所以：

- 本脚本**不再用来给「模糊阈值」找最优值** —— 那条路已被实测否掉：分块方差
  p90/p95、纹理块占比在「误报段」与「对照段」之间**每一列都重叠**
  （误报段 p90 最高 141 > 对照段最低 126），换指标解决不了「低细节 ≠ 糊」。
  `BLUR_VARIANCE_THRESHOLD` / `BLUR_RATIO_LIMIT` 现在只作**观察口径**保留。
- 它**仍然有用**：回答「这批产物是不是天然低细节」「有没有整段全判低细节」
  「黑帧比例的分位数长什么样」—— 取证用，不是调参用。
- 判据修正的依据与 44 个真实段的回放数据见技能
  `references/qc-verdict-recalibration-2026-09-18.md`。

## 为什么不能跳过（Task A6）

`app/tools/qc.py` 里这几个阈值是拍脑袋定的：
- `BLACK_RATIO_THRESHOLD = 0.95`（单帧全黑像素比例）
- `BLACK_FRAME_RATIO_LIMIT = 0.2`（帧级：黑帧占比上限）
- `FLAT_VARIANCE_THRESHOLD = 1.0`（空帧：Laplacian 方差下限，零容忍）

不标定就上线，QC 会出现「全过」或「全不过」两个极端，而后者更糟 ——
`fix_looping` 会为一批被误判的好镜反复重生，**烧真钱**。
（这正是 2026-09-18 之前发生的事：旧口径把 44 个真实段里的 7 段判失败，
其中 4 段抽帧目视 6/6 全清晰。）

## 核心陷阱：低 Laplacian 方差 ≠ 人眼觉得模糊

`blur_frame_ratio` 由 Laplacian 方差推导，它测的是**画面高频细节量**。以下情况
天然低方差但人看着完全没问题，会被误判为「模糊」：

- 大面积平坦背景（天空、雪景、纯色幕布）
- 浅景深 / 虚化光斑（刻意柔焦）
- 动漫 / 水彩 / 柔光风格
- 特写人脸（皮肤区域无纹理）

所以本脚本**必须分组看**，不能只给一个全局分位数；并显式标出
「整段所有采样帧都判模糊」的段 —— 这种极端结果几乎一定是误报而非真故障。

## 用法

    cd agent-service
    .venv/Scripts/python.exe -u scripts/calibrate_qc_thresholds.py
    # 可选：带上任务的 gen_type / prompt / style 标签，便于按风格分组
    .venv/Scripts/python.exe -u scripts/calibrate_qc_thresholds.py --meta meta.json

    # meta.json 由 mysql 导出，形如：
    #   [{"session_id": "7ea6e3c115c2", "gen_type": "text_video",
    #     "prompt": "…", "style_prompt": "国漫"}]

输出：逐指标分位数 + 逐会话明细 + 当前阈值下的判定分布 + 疑似误报清单。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# 允许 `python scripts/xxx.py` 直接跑（把 agent-service 根加进 sys.path）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tools.qc import (  # noqa: E402
    BLACK_RATIO_THRESHOLD,
    BLUR_RATIO_LIMIT,
    BLUR_VARIANCE_THRESHOLD,
    analyze_video_frames,
)

# 采样计数 bug 已修（tools/qc.py）：此前 total_frames 恒为 1（只看首帧），
# 导致 blur_frame_ratio 呈 0/1 双峰 —— 那不是质量分布，是「第一帧的结论」。
# 本脚本的百分位数据必须在修复之后采集才有意义。


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    s = sorted(values)

    def pick(q: float) -> float:
        idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        return s[idx]

    return {
        "min": s[0], "p25": pick(0.25), "p50": pick(0.50),
        "p75": pick(0.75), "p95": pick(0.95), "max": s[-1],
    }


def _fmt(d: dict[str, float]) -> str:
    return "  ".join(f"{k}={v:.4f}" for k, v in d.items()) if d else "(无样本)"


def _load_meta(path: Path | None) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(r.get("session_id")): r for r in data if r.get("session_id")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default=str(ROOT / "data" / "outputs"))
    ap.add_argument("--meta", default=None,
                    help="可选的会话标签 JSON（gen_type/prompt/style_prompt）")
    ap.add_argument("--include-final", action="store_true",
                    help="同时统计 final.mp4（默认只看 seg_*.mp4）")
    ap.add_argument("--min-kb", type=int, default=100,
                    help="小于该体积的文件直接跳过（默认 100KB，用于滤掉测试残留的 1KB 占位文件）")
    args = ap.parse_args()

    root = Path(args.outputs)
    if not root.exists():
        print(f"[!] 产物目录不存在: {root}")
        return 1

    meta = _load_meta(Path(args.meta)) if args.meta else {}
    pattern = "*.mp4" if args.include_final else "seg_*.mp4"
    all_files = sorted(root.glob(f"*/{pattern}"))
    min_bytes = args.min_kb * 1024
    files = [f for f in all_files if f.stat().st_size >= min_bytes]
    skipped_small = len(all_files) - len(files)
    if not files:
        print(f"[!] 没找到 {pattern}（≥{args.min_kb}KB）：{root}")
        print("    （A6 不可跳过，但样本为 0 时只能先攒样本）")
        return 1

    print(f"样本：{len(files)} 个文件，来自 {len({f.parent.name for f in files})} 个会话")
    if skipped_small:
        print(f"      （已滤掉 {skipped_small} 个 <{args.min_kb}KB 的文件，通常是测试残留占位）")
    print(f"当前阈值：BLACK_RATIO_THRESHOLD={BLACK_RATIO_THRESHOLD}  "
          f"BLUR_VARIANCE_THRESHOLD={BLUR_VARIANCE_THRESHOLD}  "
          f"BLUR_RATIO_LIMIT={BLUR_RATIO_LIMIT}")
    print("=" * 78)

    rows: list[dict] = []
    for f in files:
        try:
            r = analyze_video_frames(str(f))
        except Exception as exc:
            print(f"[跳过] {f.parent.name}/{f.name}: {type(exc).__name__}: {exc}")
            continue
        rows.append({
            "session": f.parent.name, "file": f.name,
            "size_mb": f.stat().st_size / 1048576,
            "black": float(r.get("black_frame_ratio") or 0.0),
            "blur": float(r.get("blur_frame_ratio") or 0.0),
            "frames": int(r.get("total_frames") or 0),
            "passed": bool(r.get("passed")),
        })

    if not rows:
        print("[!] 全部样本都无法解析（0 字节 / 损坏）")
        return 1

    blacks = [r["black"] for r in rows]
    blurs = [r["blur"] for r in rows]

    print("\n【全局分位数】")
    print("  black_frame_ratio:", _fmt(_percentiles(blacks)))
    print("  blur_frame_ratio :", _fmt(_percentiles(blurs)))

    print("\n【当前阈值下的判定分布】")
    n_black_over = sum(1 for b in blacks if b > BLACK_RATIO_THRESHOLD)
    n_blur_over = sum(1 for b in blurs if b > BLUR_RATIO_LIMIT)
    n_all_blur = sum(1 for b in blurs if b >= 1.0)
    print(f"  black > {BLACK_RATIO_THRESHOLD}: {n_black_over}/{len(rows)}")
    print(f"  blur  > {BLUR_RATIO_LIMIT} : {n_blur_over}/{len(rows)}")
    print(f"  **blur == 1.0（整段全判模糊）: {n_all_blur}/{len(rows)}  ← 这个比例高就是误报信号**")

    print("\n【逐会话明细】（同会话≈同风格/同提示词，据此判断是否需要分风格阈值）")
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(r["session"], []).append(r)
    for sid, rs in sorted(by_session.items(), key=lambda kv: -len(kv[1])):
        m = meta.get(sid, {})
        label = f" genType={m.get('gen_type')}" if m else ""
        style = f" style={m.get('style_prompt')}" if m.get("style_prompt") else ""
        bl = [r["blur"] for r in rs]
        bk = [r["black"] for r in rs]
        print(f"  {sid}  n={len(rs)}{label}{style}")
        print(f"      blur  med={statistics.median(bl):.4f} "
              f"min={min(bl):.4f} max={max(bl):.4f}")
        print(f"      black med={statistics.median(bk):.4f} max={max(bk):.4f}")
        if m.get("prompt"):
            print(f"      prompt={str(m['prompt'])[:60]}")

    # 疑似误报：被当前阈值判失败的段 —— 需要人工播一遍核对
    suspects = [r for r in rows if r["blur"] > BLUR_RATIO_LIMIT
                or r["black"] > BLACK_RATIO_THRESHOLD]
    print(f"\n【疑似误报核对清单】{len(suspects)} 个段被当前阈值判失败，请人工播一遍确认")
    print("  判据：若人眼看着清晰 → 是误报 → 阈值太严；")
    print("        若误报率 > 20% → 先修阈值，**不要进入 fix_looping 的提示词后缀设计**")
    print("        （诊断都不准，谈治疗没有意义）")
    for r in suspects[:20]:
        why = []
        if r["blur"] > BLUR_RATIO_LIMIT:
            why.append(f"blur={r['blur']:.2f}")
        if r["black"] > BLACK_RATIO_THRESHOLD:
            why.append(f"black={r['black']:.2f}")
        print(f"  {r['session']}/{r['file']}  frames={r['frames']}  "
              f"{r['size_mb']:.1f}MB  {' '.join(why)}")
    if len(suspects) > 20:
        print(f"  … 其余 {len(suspects) - 20} 个省略")

    print("\n【建议】")
    blur_p95 = _percentiles(blurs).get("p95", 0.0)
    print(f"  - 若「判模糊的帧比例」本身不可信（疑似误报多），优先改 tools/qc.py 里的")
    print(f"    BLUR_VARIANCE_THRESHOLD（当前 {BLUR_VARIANCE_THRESHOLD}），而不是调 "
          f"BLUR_RATIO_LIMIT")
    print(f"  - 当前样本 blur 比例 p95={blur_p95:.3f}；若 p95 已经接近 1.0，")
    print(f"    说明多数段都被判为「模糊帧过半」，阈值几乎失效")
    print("  - 分风格差异明显时，应把阈值做成按 visual_style 查表，")
    print("    而不是全局一个值（本项目支持电影写实/国漫等风格）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
