"""产物目录手动清理：按会话年龄删除分段/成片，控制磁盘增长。

## 为什么是手动脚本，而不是自动删

实测（2026-09-15，A8 现场测量，已排除测试残留）：
- `data/outputs/` 真实会话 5 个 / 133 MB（9/5、9/6、9/14 三天产生）
- 其中 `seg_*.mp4` 21 个 / 91.2 MB（68%）；`final.mp4` 4 个 / 40.7 MB
- **单任务约 27 MB**（7.7~42 MB，与镜数成正比）

单任务 27 MB 不足以支撑「自动删除」这种不可逆动作。而删段的代价很实在：
1. **A6 标定脚本的样本源消失** —— `calibrate_qc_thresholds.py` 遍历的就是
   `seg_*.mp4`；删掉后将来重新标定阈值无样本可用（死锁）
2. 段文件是**唯一未经二次编码的母本**（`final.mp4` 经 libx264 crf23 重编码），
   且 agnes 直链会过期 —— 删了就永久丢失
3. QC 证据链断裂：用户看到「第 2 镜模糊」无法自己播一遍确认

所以策略是：**默认保留，需要时手动跑本脚本**。

## 用法

    # 先看会删什么（默认 dry-run，不传 --apply 绝不删）
    python scripts/clean_outputs.py --older-than 30d

    # 真删：只删 30 天前的 seg_*.mp4，保留成片
    python scripts/clean_outputs.py --older-than 30d --keep-final --apply

    # 连成片一起删（整个会话目录）—— 更激进，慎用
    python scripts/clean_outputs.py --older-than 180d --no-keep-final --apply

安全约束：
- **默认 dry-run**，必须显式 `--apply` 才真删
- 年龄按会话目录 mtime 判断，且只处理 `--min-age` 之后的目录
- 永不删除 `data/outputs` 根目录本身，也永不递归到会话目录之外
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_DUR_RE = re.compile(r"^(\d+)\s*([dh])?$", re.IGNORECASE)


def parse_age(text: str) -> float:
    """把 '30d' / '12h' / '30'（默认天）解析成秒。"""
    m = _DUR_RE.match(str(text).strip())
    if not m:
        raise argparse.ArgumentTypeError(f"无法解析年龄: {text}（示例：30d / 12h / 30）")
    n = int(m.group(1))
    unit = (m.group(2) or "d").lower()
    return n * (86400 if unit == "d" else 3600)


def plan_cleanup(root: Path, max_age_s: float, keep_final: bool,
                 now: float | None = None) -> list[dict]:
    """算出该删哪些文件（**不做任何删除**，便于测试与 dry-run 复用）。"""
    now = time.time() if now is None else now
    actions: list[dict] = []
    if not root.exists():
        return actions

    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        age = now - d.stat().st_mtime
        if age < max_age_s:
            continue
        if keep_final:
            # 只删分段视频，保留成片（final.mp4 是对外服务的产物）
            for f in sorted(d.glob("seg_*.mp4")):
                actions.append({"path": f, "session": d.name,
                                "age_days": age / 86400, "kind": "seg"})
        else:
            total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            actions.append({"path": d, "session": d.name,
                            "age_days": age / 86400, "kind": "session_dir",
                            "bytes": total})
    return actions


def _human(n: float) -> str:
    return f"{n / 1048576:.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description="产物目录手动清理（默认 dry-run）")
    ap.add_argument("--outputs", default=str(ROOT / "data" / "outputs"))
    ap.add_argument("--older-than", type=parse_age, default=parse_age("30d"),
                    help="只处理早于该年龄的会话（30d / 12h / 30）。默认 30d")
    ap.add_argument("--keep-final", dest="keep_final", action="store_true",
                    default=True, help="只删 seg_*.mp4，保留 final.mp4（默认）")
    ap.add_argument("--no-keep-final", dest="keep_final", action="store_false",
                    help="删除整个会话目录（含成片）—— 不可逆，慎用")
    ap.add_argument("--apply", action="store_true",
                    help="真正执行删除（不传则只报告，绝不删）")
    args = ap.parse_args()

    root = Path(args.outputs)
    if not root.exists():
        print(f"[!] 产物目录不存在: {root}")
        return 1
    if root.resolve() == Path(root.anchor):
        print("[!] 拒绝在磁盘根目录上运行")
        return 1

    actions = plan_cleanup(root, args.older_than, args.keep_final)
    if not actions:
        print(f"没有需要清理的内容（阈值 {args.older_than / 86400:.0f} 天，"
              f"keep_final={args.keep_final}）")
        return 0

    total_bytes = 0
    for a in actions:
        if a["kind"] == "seg":
            size = a["path"].stat().st_size
        else:
            size = a.get("bytes", 0)
        total_bytes += size

    print(f"阈值：{args.older_than / 86400:.0f} 天   keep_final={args.keep_final}   "
          f"mode={'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"将处理 {len(actions)} 项，共 {_human(total_bytes)}")
    print("-" * 70)
    for a in actions[:30]:
        size = a["path"].stat().st_size if a["kind"] == "seg" else a.get("bytes", 0)
        print(f"  [{a['kind']}] {a['session']}/{a['path'].name}  "
              f"{a['age_days']:.1f}天  {_human(size)}")
    if len(actions) > 30:
        print(f"  … 其余 {len(actions) - 30} 项省略")

    if not args.apply:
        print("-" * 70)
        print("这是 dry-run，什么都没删。确认无误后加 --apply 再跑一次。")
        return 0

    removed = 0
    for a in actions:
        try:
            if a["kind"] == "seg":
                a["path"].unlink()
            else:
                shutil.rmtree(a["path"])
            removed += 1
        except Exception as exc:
            print(f"  [失败] {a['path']}: {type(exc).__name__}: {exc}")
    print("-" * 70)
    print(f"已删除 {removed}/{len(actions)} 项，释放约 {_human(total_bytes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
