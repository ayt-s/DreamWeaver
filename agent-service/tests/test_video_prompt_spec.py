"""视频提示词规范（Agnes Video 2.5 提示词模板指南 v1.0 · 2026-09-08）落地护栏。

★ 2026-09-24 落地前实测基线（读 agent.log 里 3 条真实 `submit_video payload`）：
  - 提交的提示词 = 图像提示词整段翻译 + 运镜 + 红线，**没有时间轴**、
    **没有任何声音指令**；
  - 而产物 ffprobe 全是 h264+aac —— BGM 由模型自由发挥、**段段不同**。

本文件锁住四条口径：
  1. 视频提示词单开字段 `video_prompt_en`（`prompt_en` 继续喂出图，两者不混用）
  2. 声音段：BGM 开关**关** → 明确排除背景音乐；**开** → 不写（交给模型自行配乐）
  3. keyframe（只给首帧）→ 补一句首帧锁定说明，且两个提示词字段都要清掉悬空的 <Picture N>
  4. video_generator 取 `video_prompt_en`，缺失时回退 `prompt_en`（老会话）
"""

import json

import pytest

from app.nodes import storyboard as sb
from app.nodes import video as vb
from app.novel import composer
from app.utils import prompting

CHAR = "https://cdn.example.com/char.png"
FIRST = "https://cdn.example.com/first.png"


async def _noop(*_args, **_kwargs):
    return None


async def _echo_translate(text: str) -> str:
    """把 LLM 输入原样返回 —— 这样断言能直接看到「送给模型的模板要求」。"""
    return str(text).strip()


@pytest.fixture(autouse=True)
def _silence_events(monkeypatch):
    monkeypatch.setattr("app.events.emit", _noop)


def _canvas_state(**extra):
    state = {
        "session_id": "t",
        "segments": [{"image_url": FIRST, "prompt": "少年走进山洞", "seconds": 5}],
        "trace": [],
    }
    state.update(extra)
    return state


# ============================ 单元：声音段 / 时长头 ============================

def test_sound_clause_excludes_bgm_when_switch_off():
    clause = prompting.sound_clause(False)
    assert clause == prompting.SOUND_NO_BGM_EN
    assert "background music" in clause


def test_sound_clause_is_empty_when_bgm_on():
    """开关打开时不写任何声音句：文档明确「要音乐」与「不要 BGM」同时出现是自相矛盾的。"""
    assert prompting.sound_clause(True) == ""


def test_duration_head_carries_seconds_and_frame():
    assert prompting.video_duration_head(5, "16:9") == "5 seconds, 16:9 horizontal"
    assert prompting.video_duration_head("8", "9:16") == "8 seconds, 9:16 vertical"
    # 脏值回落，不能把 None/空串拼进提示词
    assert prompting.video_duration_head(None, "") == "5 seconds, 16:9 horizontal"


def test_rewrite_input_demands_timeline_sound_and_english():
    text = prompting.build_video_rewrite_input("少年走进山洞", 5, "16:9")
    assert "5 seconds, 16:9 horizontal" in text
    assert "0-2s" in text, "模板必须要求时间轴分段（文档的画面过程说明）"
    assert "sound" in text.lower(), "模板必须要求声音段"
    assert "English only" in text, "必须约束全英文（此前出现过中文残留）"
    assert "少年走进山洞" in text
    assert "{" not in text, "模板占位符必须全部填掉"


def test_rewrite_input_carries_camera_inside_the_template():
    """运镜注入模板内部（不是生成后追加）：事后追加会重复运镜、并把排除句挤出末位。"""
    text = prompting.build_video_rewrite_input(
        "少年走进山洞", 5, "16:9", camera_en="medium shot, slow push-in")
    assert "medium shot, slow push-in" in text


def test_rewrite_input_without_camera_falls_back_to_text():
    text = prompting.build_video_rewrite_input("少年走进山洞", 5, "16:9")
    assert "as described in the Chinese text" in text


# ============================ 画布节点：两个提示词字段 ============================

@pytest.mark.asyncio
async def test_canvas_storyboarder_writes_separate_video_prompt(monkeypatch):
    monkeypatch.setattr(sb, "translate_to_en", _echo_translate)

    shot = (await sb.canvas_storyboarder_node(_canvas_state()))["storyboard"][0]

    assert shot["video_prompt_en"], "必须单开视频提示词字段"
    assert "5 seconds, 16:9 horizontal" in shot["video_prompt_en"], "视频提示词要有时长+画幅头"
    assert prompting.SOUND_NO_BGM_EN in shot["video_prompt_en"], "默认不加 BGM → 必须写明排除"
    # 图像那条不能被视频专属内容污染（标准链路上它还要喂 image_generator 出图）
    assert "5 seconds, 16:9 horizontal" not in shot["prompt_en"]
    assert prompting.SOUND_NO_BGM_EN not in shot["prompt_en"]


@pytest.mark.asyncio
async def test_bgm_switch_on_drops_the_exclusion(monkeypatch):
    monkeypatch.setattr(sb, "translate_to_en", _echo_translate)

    out = await sb.canvas_storyboarder_node(_canvas_state(bgm=True))

    assert prompting.SOUND_NO_BGM_EN not in out["storyboard"][0]["video_prompt_en"]


@pytest.mark.asyncio
async def test_keyframe_adds_first_frame_clause_and_cleans_both_prompts(monkeypatch):
    monkeypatch.setattr(sb, "translate_to_en", _echo_translate)
    state = _canvas_state(reference_bindings=[{"name": "陈浔", "imageUrl": CHAR}])
    state["segments"] = [{"image_url": FIRST, "prompt": "陈浔走进山洞",
                          "reference_images": [FIRST, CHAR], "seconds": 5}]

    shot = (await sb.canvas_storyboarder_node(state))["storyboard"][0]

    assert shot["mode"] == "keyframe"
    assert prompting.FIRST_FRAME_EN in shot["video_prompt_en"], "keyframe 要写明只延展首帧"
    for field in ("prompt_en", "video_prompt_en"):
        assert "<Picture" not in shot[field], f"{field} 里不能留悬空的 <Picture N>（keyframe 不带图）"


@pytest.mark.asyncio
async def test_bindings_survive_when_not_keyframe(monkeypatch):
    """不锁首帧 → reference 模式，绑定指向真实存在的图，必须留着。"""
    monkeypatch.setattr(sb, "translate_to_en", _echo_translate)
    state = _canvas_state(lock_first_frame=False,
                          reference_bindings=[{"name": "陈浔", "imageUrl": CHAR}])
    state["segments"] = [{"prompt": "陈浔走进山洞", "reference_images": [CHAR], "seconds": 5}]

    shot = (await sb.canvas_storyboarder_node(state))["storyboard"][0]

    assert shot["mode"] == "reference"
    assert "<Picture 1>" in shot["video_prompt_en"]


@pytest.mark.asyncio
async def test_standard_storyboarder_also_writes_video_prompt(monkeypatch):
    """标准链路（text_video）同样要有视频提示词 —— 否则它仍提交图像提示词。"""
    monkeypatch.setattr(sb, "translate_to_en", _echo_translate)
    state = {
        "session_id": "t",
        "script": [{"shot_id": 0, "visual": "少年走进山洞", "camera": "中景固定",
                    "style_note": "3D 写实国漫", "duration": 6}],
    }

    shot = (await sb.storyboarder_node(state))["storyboard"][0]

    assert "6 seconds, 16:9 horizontal" in shot["video_prompt_en"]
    assert prompting.SOUND_NO_BGM_EN in shot["video_prompt_en"]
    assert "6 seconds" not in shot["prompt_en"]


@pytest.mark.asyncio
async def test_segment_rework_reuses_both_prompts(monkeypatch):
    """段重生：两个字段都在 → 一次 LLM 都不该调（否则白烧额度）。"""

    async def _boom(_text: str) -> str:
        raise AssertionError("段重生不该再调 LLM")

    monkeypatch.setattr(sb, "translate_to_en", _boom)
    state = _canvas_state()
    state["segments"] = [{"prompt": "少年走进山洞", "seconds": 5,
                          "prompt_en": "IMG", "video_prompt_en": "VID"}]

    shot = (await sb.canvas_storyboarder_node(state))["storyboard"][0]

    assert shot["prompt_en"] == "IMG"
    assert shot["video_prompt_en"] == "VID"


# ============================ 出口：video_generator 取哪条 ============================

def test_video_generator_prefers_video_prompt():
    assert vb._effective_prompt({"video_prompt_en": "V", "prompt_en": "I"}) == "V"


def test_video_generator_falls_back_for_old_sessions():
    assert vb._effective_prompt({"prompt_en": "I"}) == "I"


def test_fix_hint_still_appended_to_video_prompt():
    assert vb._effective_prompt({"video_prompt_en": "V", "fix_hint": " H"}) == "V H"


# ============================ 段重生：video_prompt_en 必须能穿过 API 白名单 ============================

def test_parse_segments_keeps_video_prompt():
    """`_parse_segments` 是**白名单** dict —— 不加这个键，Java 侧保留的复用译文会被静默丢掉。"""
    from app.main import _parse_segments

    segs = _parse_segments(json.dumps([{
        "image_url": "http://x/a.png", "prompt": "描述",
        "prompt_en": "IMG", "video_prompt_en": "VID", "seconds": 5,
    }]))

    assert segs[0]["prompt_en"] == "IMG"
    assert segs[0]["video_prompt_en"] == "VID", "复用段要能跳过视频改写那次 LLM 调用"


# ============================ 小说链路的中文视频提示词 ============================

def test_compose_video_prompt_has_duration_head_and_sound():
    out = composer.compose_video_prompt(
        {"plot": "少年走进山洞", "scene": "山洞，白日", "seconds": 6}, "3D 写实国漫")
    assert out.startswith("时长 6 秒，16:9 横版。")
    assert prompting.SOUND_NO_BGM_CN in out


def test_compose_video_prompt_bgm_on_has_no_exclusion():
    out = composer.compose_video_prompt(
        {"plot": "少年走进山洞", "seconds": 6}, "3D 写实国漫", bgm=True)
    assert prompting.SOUND_NO_BGM_CN not in out
    assert out.startswith("时长 6 秒，16:9 横版。")


# ============================ 台词（文档速查表第 2 条「台词 = 原文」）============================

def test_dialogue_hint_none_when_no_dialogue():
    assert "no dialogue" in prompting.dialogue_hint("", "")


def test_dialogue_hint_marks_on_screen_and_offscreen():
    on = prompting.dialogue_hint("今晚的海面，好像藏着什么。", "陈浔")
    assert "character for character" in on and "陈浔" in on and "lip-sync" in on
    off = prompting.dialogue_hint("先别打开那扇门。", "画外音")
    assert "voice-over" in off and "NOT shown talking" in off
    assert prompting.is_offscreen("画外音") and not prompting.is_offscreen("陈浔")


def test_template_declares_dialogue_as_input_spec():
    """实测教训：台词那行曾被模型**当成正文整段抄进输出**（还把中文标签留在英文提示词里）。

    所以模板必须声明它是输入规格、不许照抄；同时第 8 条要给台词开一个**逐字**例外
    （否则「全英文」会把台词翻译掉，与「台词 = 原文」直接冲突）。
    """
    text = prompting.build_video_rewrite_input("走", 5, "16:9", dialogue="台词", speaker="陈浔")
    assert "never copy this block" in text
    assert "EXCEPT the dialogue line" in text


def test_rewrite_input_carries_dialogue_verbatim():
    line = "今晚的海面，好像藏着什么。"
    text = prompting.build_video_rewrite_input(
        "少年走向灯塔", 8, "16:9", dialogue=line, speaker="陈浔")
    assert line in text, "台词必须逐字进模板（翻译会改写，不能混进中文描述）"
    assert "VERBATIM" in text.upper()


def test_dialogue_clause_cn_marks_offscreen():
    cn = prompting.dialogue_clause_cn("先别打开那扇门。", "画外音")
    assert "画外音" in cn and "先别打开那扇门。" in cn
    assert prompting.dialogue_clause_cn("", "") == ""


@pytest.mark.asyncio
async def test_canvas_storyboarder_injects_dialogue_into_video_prompt(monkeypatch):
    monkeypatch.setattr(sb, "translate_to_en", _echo_translate)
    state = _canvas_state()
    state["segments"] = [{"image_url": FIRST, "prompt": "陈浔站在山坡上", "seconds": 5,
                          "dialogue": "今晚的海面，好像藏着什么。", "dialogue_speaker": "陈浔"}]

    shot = (await sb.canvas_storyboarder_node(state))["storyboard"][0]

    assert "今晚的海面，好像藏着什么。" in shot["video_prompt_en"]
    assert shot["dialogue"] == "今晚的海面，好像藏着什么。"
    assert shot["dialogue_speaker"] == "陈浔"


@pytest.mark.asyncio
async def test_canvas_storyboarder_tells_model_when_no_dialogue(monkeypatch):
    monkeypatch.setattr(sb, "translate_to_en", _echo_translate)

    shot = (await sb.canvas_storyboarder_node(_canvas_state()))["storyboard"][0]

    assert "no dialogue" in shot["video_prompt_en"], "无台词也要写明，免得模型自己编对白"
    assert shot["dialogue"] == ""


def test_parse_segments_keeps_dialogue():
    from app.main import _parse_segments

    segs = _parse_segments(json.dumps([{
        "image_url": "http://x/a.png", "prompt": "描述", "seconds": 5,
        "dialogue": "台词原文", "dialogueSpeaker": "陈浔",
    }]))

    assert segs[0]["dialogue"] == "台词原文"
    assert segs[0]["dialogue_speaker"] == "陈浔", "camelCase 也要认"


def test_novel_segment_schema_has_dialogue_defaults():
    """小说分镜 schema：LLM 漏给台词时必须是空串，不能让校验炸掉整次预处理。"""
    from app.novel.storyboarder import NovelSegmentPydantic

    seg = NovelSegmentPydantic(id="s1", chapter=1, title="山坡", plot="少年走上山坡",
                               characters=["陈浔"], scene="小山村山坡，清晨", camera="中景",
                               seconds=5, mood="平静")
    assert seg.dialogue == "" and seg.dialogue_speaker == ""


def test_compose_video_prompt_includes_dialogue_when_present():
    out = composer.compose_video_prompt(
        {"plot": "少年走向灯塔", "seconds": 8,
         "dialogue": "今晚的海面，好像藏着什么。", "dialogue_speaker": "画外音"}, "电影写实")
    assert "【台词】" in out and "画外音" in out and "今晚的海面，好像藏着什么。" in out


def test_compose_video_prompt_omits_dialogue_clause_when_absent():
    out = composer.compose_video_prompt({"plot": "少年走上山坡", "seconds": 5}, "电影写实")
    assert "【台词】" not in out
