"""app/utils/media.py 的单元测试。

背景（A1）：下载/探测/拼接/输出目录原先都在 nodes/synthesizer.py 内部，
asset_fetch（QC 需要本地文件）与 image_slideshow 都要用，导致「节点 import 节点」。
抽到 utils 层后需要保证：
1. output_root() 是函数而非模块级常量 —— 这样才能被 monkeypatch.setenv 覆盖
   （原 synthesizer.py 的 OUTPUT_ROOT 是 import 时求值的常量，改
   DREAMWEAVER_OUTPUT_DIR 不生效，测试也覆盖不了）
2. ffmpeg_exe() 能拿到真实可执行的 ffmpeg
3. local_url() 的形状与 main.py 挂载的 /v1/files 一致（前端直接 <video> 播放）
"""
from pathlib import Path

from app.utils import media


def test_output_root_follows_env(monkeypatch, tmp_path):
    """环境变量改了必须立刻生效（不能是 import 时锁死的常量）。"""
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))
    assert media.output_root() == tmp_path


def test_output_root_default_points_to_agent_service_data_outputs(monkeypatch):
    monkeypatch.delenv("DREAMWEAVER_OUTPUT_DIR", raising=False)
    assert media.output_root().as_posix().endswith("agent-service/data/outputs")


def test_session_dir_creates_and_returns_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DREAMWEAVER_OUTPUT_DIR", str(tmp_path))
    d = media.session_dir("abc123")
    assert d.exists()
    assert d.is_dir()
    assert d.name == "abc123"
    # 幂等：重复调用不报错
    assert media.session_dir("abc123") == d


def test_local_url_shape():
    """与 main.py:55 的 app.mount("/v1/files", ...) 契约一致。"""
    assert media.local_url("abc123") == "/v1/files/abc123/final.mp4"


def test_ffmpeg_exe_is_a_real_executable():
    exe = Path(media.ffmpeg_exe())
    assert exe.exists(), f"ffmpeg 不存在: {exe}"
    assert exe.stat().st_size > 0


def test_ffmpeg_exe_is_cached():
    """get_ffmpeg_exe() 有磁盘检查开销，必须缓存（同一值、同一对象）。"""
    assert media.ffmpeg_exe() is media.ffmpeg_exe()
