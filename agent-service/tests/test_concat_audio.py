"""拼接（xfade）不丢音频 / 混 fps 不降级的回归测试（2026-09-18）。

锁住两条**实测撞出来**的硬要求，它们都属于「读代码看不出来、只有跑真产物才发现」：

1. **音频必须接进 xfade 链**。此前 `filter_complex` 只建视频链、`-map` 只给 `[vout]`，
   于是多段成片整条没有声音 —— 平台给的段其实带 aac 音轨。
   实测：14/14 段有音轨，成片 9/10 没有；唯一有音轨的那条是**单段**（走 copyfile）。
   这条测试就是防止有人再把 `-map [aout]` 删掉。
2. **每路必须先 `fps=` 归一化**，且**不能**用 `setpts`。
   xfade 要求两个输入 timebase 一致：24fps 真实段 + 30fps 幻灯片段会报
   `timebase (1/12288) do not match (1/15360)` 并整条失败 → 降级硬切（过渡没了）。
   而 `setpts=PTS-STARTPTS` 会把链路帧率清成 1/0，xfade 报
   `current rate of 1/0 is invalid`（对照组：只 fps / 只 null / 输入前 -r 都正常）。
"""
import pytest

from app.utils import media
from app.utils.proc import CommandResult


class _FakeFFmpeg:
    """假 run_command：记录每条命令、把末位参数当输出文件建出来、按需回带 Audio:。"""

    def __init__(self, output_has_audio: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.output_has_audio = output_has_audio

    async def __call__(self, cmd, timeout=None, **kwargs):
        args = [str(c) for c in cmd]
        self.calls.append(args)
        last = args[-1]
        if not last.startswith("-"):
            from pathlib import Path
            p = Path(last)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
        stderr = "Stream #0:1: Audio: aac" if self.output_has_audio else ""
        return CommandResult(returncode=0, stdout="", stderr=stderr)


def _patch(monkeypatch, infos, output_has_audio=True, loudness=None):
    """infos: 每个输入一段 [(秒, 是否有音轨, fps), ...]（按调用顺序消费）。

    loudness: 每个**带音轨**的输入一段 (I/LUFS, TP/dBTP) 或 None（探测失败），
              按调用顺序消费；不给则一律返回 None（= 不做增益，与旧行为一致）。
    """
    ff = _FakeFFmpeg(output_has_audio)
    monkeypatch.setattr(media, "run_command", ff)
    monkeypatch.setattr(media, "ffmpeg_exe", lambda: "ffmpeg")
    seq = list(infos)

    async def fake_probe(_p):
        return seq.pop(0)

    async def fake_has_audio(_p):
        return output_has_audio

    lseq = list(loudness or [])

    async def fake_loudness(_p):
        return lseq.pop(0) if lseq else None

    monkeypatch.setattr(media, "probe_streams", fake_probe)
    monkeypatch.setattr(media, "probe_has_audio", fake_has_audio)
    monkeypatch.setattr(media, "probe_loudness", fake_loudness)
    return ff


def _xfade_cmd(ff: _FakeFFmpeg) -> list[str]:
    """挑出带 filter_complex 的那条命令（探测命令没有这个参数）。"""
    hits = [c for c in ff.calls if "-filter_complex" in c]
    assert hits, f"没有发出 xfade 命令：{ff.calls}"
    return hits[-1]


def _filters(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def _inputs(cmd: list[str]) -> list[str]:
    """取出所有 `-i` 后面的输入（按顺序）。"""
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]


@pytest.mark.asyncio
async def test_xfade_maps_audio_when_all_segments_have_audio(monkeypatch, tmp_path):
    """核心回归：三段都带音轨 → 命令里必须有 [aout] 与 acrossfade，且音频编码为 aac。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0)] * 3)
    ok = await media._concat_with_xfade(
        [tmp_path / f"s{i}.mp4" for i in range(3)], tmp_path / "final.mp4"
    )

    assert ok is True
    cmd = _xfade_cmd(ff)
    filters = _filters(cmd)
    assert "[aout]" in cmd, "必须把音频也 map 出去 —— 否则多段成片整条没声音（原 bug）"
    assert "-c:a" in cmd and "aac" in cmd
    assert filters.count("acrossfade") == 2, "n 段要 n-1 次 acrossfade，与 xfade 一一对应"
    assert filters.count("xfade=transition") == 2


@pytest.mark.asyncio
async def test_segment_without_audio_gets_silence_padded(monkeypatch, tmp_path):
    """混入无音轨段（图片幻灯片那种）→ 必须垫 anullsrc，否则 acrossfade 整条失败。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0), (4.5, False, 30.0), (4.5, True, 24.0)])
    ok = await media._concat_with_xfade(
        [tmp_path / f"s{i}.mp4" for i in range(3)], tmp_path / "final.mp4"
    )

    assert ok is True
    cmd = _xfade_cmd(ff)
    ins = _inputs(cmd)
    # 静音垫必须作为**第 4 个输入**（索引 3）追加在真实输入之后 —— 音频链里的 [3:a] 指的就是它
    assert len(ins) == 4, f"应为 3 个真实输入 + 1 个静音垫，实际 {ins}"
    assert ins[3].startswith("anullsrc"), f"缺音轨的段要垫静音，实际第 4 个输入是 {ins[3]!r}"
    assert "lavfi" in cmd
    assert "[3:a]" in _filters(cmd), "静音垫应作为第 4 个输入（索引 3）接进音频链"
    assert _filters(cmd).count("acrossfade") == 2
    assert "[aout]" in cmd


@pytest.mark.asyncio
async def test_all_silent_inputs_produce_no_audio_chain(monkeypatch, tmp_path):
    """全都没有音轨（纯幻灯片画布）→ 不接音频链、不报错，行为与修复前一致。"""
    ff = _patch(monkeypatch, [(4.5, False, 30.0)] * 3)
    ok = await media._concat_with_xfade(
        [tmp_path / f"s{i}.mp4" for i in range(3)], tmp_path / "final.mp4"
    )

    assert ok is True
    cmd = _xfade_cmd(ff)
    filters = _filters(cmd)
    assert "acrossfade" not in filters and "anullsrc" not in cmd
    assert "[aout]" not in cmd
    assert "[vout]" in cmd


@pytest.mark.asyncio
async def test_fps_normalized_to_first_input_for_mixed_rates(monkeypatch, tmp_path):
    """混 fps：全部归一化到首路 fps（不这样做 xfade 会因 timebase 不一致整条失败）。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0), (4.5, True, 30.0), (4.5, True, 25.0)])
    ok = await media._concat_with_xfade(
        [tmp_path / f"s{i}.mp4" for i in range(3)], tmp_path / "final.mp4"
    )

    assert ok is True
    filters = _filters(_xfade_cmd(ff))
    assert filters.count("fps=24.0") == 3, "三路都要归一化到首路的 24fps"
    assert "fps=30.0" not in filters and "fps=25.0" not in filters


@pytest.mark.asyncio
async def test_setpts_is_never_used_before_xfade(monkeypatch, tmp_path):
    """`setpts` 会把链路帧率清成 1/0 → xfade 直接拒绝，绝不能加回来。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0)] * 2)
    await media._concat_with_xfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "final.mp4"
    )

    filters = _filters(_xfade_cmd(ff))
    assert "setpts" not in filters
    assert "settb" not in filters


@pytest.mark.asyncio
async def test_audio_less_output_is_treated_as_failure(monkeypatch, tmp_path):
    """拼接"成功"但产物没音轨（输入有）→ 必须判失败，让上层降级/告警而不是悄悄交付。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0)] * 3, output_has_audio=False)
    ok = await media._concat_with_xfade(
        [tmp_path / f"s{i}.mp4" for i in range(3)], tmp_path / "final.mp4"
    )
    assert ok is False


@pytest.mark.asyncio
async def test_unprobeable_segment_degrades_to_plain_concat(monkeypatch, tmp_path):
    """探测不到时长 → 返回 False（交给 concat 硬切兜底），不能抛异常。"""
    ff = _patch(monkeypatch, [(-1.0, True, 24.0), (4.5, True, 24.0)])
    ok = await media._concat_with_xfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "final.mp4"
    )
    assert ok is False


# ---- 音量归一化（2026-09-18）-------------------------------------------------
# 实测前提：10 个真实段的整合响度 I 中位数 -28.3 LUFS、范围 -42.6 ~ -14.7，
# 即段间差 27.9 LU（>3 LU 就能明显听出忽大忽小）。所以逐段给静态增益。


@pytest.mark.asyncio
async def test_each_segment_is_gained_toward_target_lufs(monkeypatch, tmp_path):
    """三段实测值 → 各自增益把响度拉到 -16 LUFS（受峰值上限约束）。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0)] * 3,
           loudness=[(-42.6, -27.2), (-28.3, -18.0), (-16.0, -3.0)])
    ok = await media._concat_with_xfade(
        [tmp_path / f"s{i}.mp4" for i in range(3)], tmp_path / "final.mp4"
    )

    assert ok is True
    f = _filters(_xfade_cmd(ff))
    # 第 1 段：响度差 +26.6，但峰值只允许 +25.7 → 取小值再被 24dB 上限截断
    assert "volume=24.00dB" in f
    # 第 2 段：min(-16-(-28.3)=12.3, -1.5-(-18.0)=16.5) → 12.3
    assert "volume=12.30dB" in f
    # 第 3 段：已在目标（-16）→ 不加增益滤镜
    assert f.count("volume=") == 2


@pytest.mark.asyncio
async def test_peak_ceiling_limits_gain(monkeypatch, tmp_path):
    """响度需要 +22.7dB，但真实峰值只剩 0.5dB 余量 → 只能给 0.5dB（不许削波）。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0), (4.5, True, 24.0)],
           loudness=[(-38.7, -2.0), (-16.0, -3.0)])
    await media._concat_with_xfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "final.mp4"
    )
    f = _filters(_xfade_cmd(ff))
    assert "volume=0.50dB" in f
    assert "volume=22.70dB" not in f, "越过峰值上限会把削波风险一起放大"


@pytest.mark.asyncio
async def test_gain_is_clamped(monkeypatch, tmp_path):
    """极轻的段（-60 LUFS）不许拉满 44dB，按 AUDIO_GAIN_LIMIT_DB 截断。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0), (4.5, True, 24.0)],
           loudness=[(-60.0, -50.0), (-16.0, -3.0)])
    await media._concat_with_xfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "final.mp4"
    )
    f = _filters(_xfade_cmd(ff))
    assert f"volume={media.AUDIO_GAIN_LIMIT_DB:.2f}dB" in f


@pytest.mark.asyncio
async def test_near_silent_track_gets_no_gain(monkeypatch, tmp_path):
    """有音轨但实质静音（-80 LUFS）→ 不增益，否则把噪声地板抬成主角。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0), (4.5, True, 24.0)],
           loudness=[(-80.0, -70.0), (-16.0, -3.0)])
    await media._concat_with_xfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "final.mp4"
    )
    assert "volume=" not in _filters(_xfade_cmd(ff))


@pytest.mark.asyncio
async def test_loudness_probe_failure_is_non_fatal(monkeypatch, tmp_path):
    """响度探测失败 → 不加增益、但拼接照常成功（不能因为量不出来就不拼）。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0), (4.5, True, 24.0)], loudness=[None, None])
    ok = await media._concat_with_xfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "final.mp4"
    )
    assert ok is True
    assert "volume=" not in _filters(_xfade_cmd(ff))


@pytest.mark.asyncio
async def test_silence_padded_segment_gets_no_gain(monkeypatch, tmp_path):
    """垫静音的那段不该被增益（它是 anullsrc，增益只在真实音轨上做）。"""
    ff = _patch(monkeypatch, [(4.5, True, 24.0), (4.5, False, 30.0)], loudness=[(-28.0, -18.0)])
    await media._concat_with_xfade(
        [tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "final.mp4"
    )
    f = _filters(_xfade_cmd(ff))
    assert f.count("volume=") == 1, "只有带音轨的那段有增益"
