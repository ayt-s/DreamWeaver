"""回归测试：SelectorEventLoop（uvicorn --reload 在 Windows 上的事件循环）下外部命令仍可用。

背景（真实故障 2026-09-14）：
uvicorn 以 --reload 启动时 use_subprocess=True → 事件循环退化为 SelectorEventLoop；
Windows 的 SelectorEventLoop 不支持 asyncio.create_subprocess_exec（抛 NotImplementedError，
且消息为空），于是 synthesizer 的 ffmpeg 拼接与 image_slideshow 的图片转片段全部失败。
异常又被节点的兜底分支吞掉 → 任务显示 completed 却没有成片、也没有任何提示。

修复：统一走 app.utils.proc.run_command（asyncio.to_thread + subprocess.run）。
本测试确保这条路径在 Selector 循环下真的能跑完「生成片段 → 拼接成片」。
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.media import concat_videos, ffmpeg_exe, probe_duration  # noqa: E402
from app.utils.proc import run_command  # noqa: E402


@pytest.fixture
def selector_loop():
    """临时把事件循环策略切成 SelectorEventLoop（还原故障现场）。"""
    if sys.platform != "win32":
        pytest.skip("SelectorEventLoop 无子进程支持是 Windows 特有行为")
    old = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(old)


def test_run_command_on_selector_loop(selector_loop):
    """run_command 在 Selector 循环下能执行外部命令并拿到输出。"""
    async def _main():
        return await run_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('hello-stderr')"],
            timeout=60,
        )

    res = asyncio.run(_main())
    assert res.returncode == 0
    assert "hello-stderr" in res.stderr
    assert res.timed_out is False


def test_selector_loop_lacks_native_subprocess(selector_loop):
    """固化故障前提：Selector 循环下原生 create_subprocess_exec 不可用。

    这条断言一旦在未来失效（如平台/依赖变化），说明本文件的前提需要复核，
    而不是悄悄放过。
    """
    async def _main():
        with pytest.raises(NotImplementedError):
            await asyncio.create_subprocess_exec("cmd", "/c", "echo", "x")

    asyncio.run(_main())


def test_concat_under_selector_loop(tmp_path, selector_loop):
    """端到端：Selector 循环下「探测时长 → xfade 拼接」必须产出成片。"""
    clips = []
    colors = ("black", "white")
    for i, color in enumerate(colors):
        clip = tmp_path / f"clip_{i}.mp4"
        gen = asyncio.run(run_command([
            ffmpeg_exe(), "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=64x64:d=1:r=15",
            "-pix_fmt", "yuv420p", str(clip),
        ], timeout=120))
        assert gen.returncode == 0, gen.stderr[-400:]
        assert clip.exists() and clip.stat().st_size > 0
        clips.append(clip)

    async def _main():
        dur = await probe_duration(clips[0])
        out = tmp_path / "final.mp4"
        ok = await concat_videos(clips, out)
        return dur, ok, out

    duration, ok, final = asyncio.run(_main())
    assert duration > 0, "Selector 循环下时长探测失败"
    assert ok is True, "Selector 循环下拼接失败（即本次故障未修复）"
    assert final.exists() and final.stat().st_size > 0
