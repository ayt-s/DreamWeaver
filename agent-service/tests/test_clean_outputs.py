"""clean_outputs 的清理计划测试。

删除类脚本必须有测试：`plan_cleanup` 是纯函数（不改文件系统），
所以这里**只测计划**，真删路径留给人工在 dry-run 确认后执行。
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import clean_outputs as co  # noqa: E402


def _mk_session(root: Path, name: str, age_days: float, n_seg: int = 2,
                with_final: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_seg):
        (d / f"seg_{i:03d}.mp4").write_bytes(b"x" * 1024)
    if with_final:
        (d / "final.mp4").write_bytes(b"y" * 2048)
    ts = time.time() - age_days * 86400
    os.utime(d, (ts, ts))
    return d


def test_parse_age_units():
    assert co.parse_age("30d") == 30 * 86400
    assert co.parse_age("12h") == 12 * 3600
    assert co.parse_age("30") == 30 * 86400          # 默认天
    assert co.parse_age("1D") == 86400               # 大小写不敏感
    with pytest.raises(Exception):
        co.parse_age("abc")


def test_keep_final_only_plans_seg_files(tmp_path):
    _mk_session(tmp_path, "old-session", age_days=40, n_seg=3)
    _mk_session(tmp_path, "new-session", age_days=1, n_seg=3)

    actions = co.plan_cleanup(tmp_path, co.parse_age("30d"), keep_final=True)

    assert len(actions) == 3, "只应计划 old-session 的 3 个 seg"
    assert all(a["kind"] == "seg" for a in actions)
    assert all(a["session"] == "old-session" for a in actions)
    assert all("final.mp4" not in a["path"].name for a in actions), "成片绝不能进计划"


def test_no_keep_final_plans_whole_session_dir(tmp_path):
    _mk_session(tmp_path, "old-session", age_days=40, n_seg=3)

    actions = co.plan_cleanup(tmp_path, co.parse_age("30d"), keep_final=False)

    assert len(actions) == 1
    assert actions[0]["kind"] == "session_dir"
    assert actions[0]["path"].name == "old-session"
    assert actions[0]["bytes"] > 0


def test_recent_sessions_are_never_touched(tmp_path):
    _mk_session(tmp_path, "fresh", age_days=2, n_seg=5)

    assert co.plan_cleanup(tmp_path, co.parse_age("30d"), keep_final=True) == []
    assert co.plan_cleanup(tmp_path, co.parse_age("30d"), keep_final=False) == []


def test_age_boundary_is_inclusive(tmp_path):
    """边界语义：`age >= 阈值` 才删 —— 恰好等于阈值会被删。

    用显式 `now` 而不是依赖真实时钟：`_mk_session` 把 mtime 设成「N 天前」，
    但等到 plan_cleanup 执行时 now 已前进几微秒，靠真实时钟的边界断言必然抖动。
    """
    d = _mk_session(tmp_path, "borderline", age_days=30, n_seg=1)
    mtime = d.stat().st_mtime
    threshold = 30 * 86400

    # now 恰好 = mtime + 30 天 → age == 阈值 → 删
    assert len(co.plan_cleanup(tmp_path, threshold, keep_final=True,
                               now=mtime + threshold)) == 1
    # now 比上面早 1 秒 → age 略小于阈值 → 不删
    assert co.plan_cleanup(tmp_path, threshold, keep_final=True,
                           now=mtime + threshold - 1) == []


def test_session_without_seg_files_yields_nothing_in_keep_final_mode(tmp_path):
    """成片目录（图片任务/合成视频）没有 seg，keep_final 模式下不该有计划。"""
    _mk_session(tmp_path, "slideshow-only", age_days=99, n_seg=0, with_final=True)

    assert co.plan_cleanup(tmp_path, co.parse_age("30d"), keep_final=True) == []


def test_missing_root_returns_empty(tmp_path):
    assert co.plan_cleanup(tmp_path / "nope", co.parse_age("1d"), keep_final=True) == []


def test_plan_does_not_delete_anything(tmp_path):
    """plan_cleanup 必须是纯计划 —— 核对文件仍在磁盘上。"""
    d = _mk_session(tmp_path, "old-session", age_days=40, n_seg=2)

    co.plan_cleanup(tmp_path, co.parse_age("30d"), keep_final=True)

    assert len(list(d.glob("seg_*.mp4"))) == 2
    assert (d / "final.mp4").exists()
