"""prompt_lint 单测 —— 每条判据都要「命中」与「不命中」两侧都验。

判据是关键词启发式，只验命中会掩盖误报；只验不命中会掩盖空转。两侧齐验才算有效。
"""

from __future__ import annotations

import pytest

from app.utils.prompt_lint import (
    has_fail,
    lint_cn_shot,
    lint_segment,
    lint_video_prompt_en,
)

GOOD_CN = "中景，陈浔蹲在灶前拨火，炊烟缓缓升起，镜头缓慢推进，暖色晨光。"
GOOD_EN = (
    "5 seconds, 16:9 horizontal. A medium shot of Chen Xun tending a fire. "
    "0-2s: he pokes the fire; smoke rises. 2-5s: smoke thickens as he works. "
    "Ambient sounds of crackling fire. "
    "No extra background music; keep only natural ambient sounds and action sounds."
)


def _codes(issues):
    return [i["code"] for i in issues]


# ---------------- 中文层 ----------------

def test_bare_negation_hits_and_clean_text_passes():
    issues = lint_cn_shot("玉佩不发光，面无笑意。")
    assert "CN_BARE_NEGATION" in _codes(issues)
    assert lint_cn_shot(GOOD_CN) == [] or "CN_BARE_NEGATION" not in _codes(lint_cn_shot(GOOD_CN))


def test_gaze_on_prop_hits_unless_gaze_written():
    hit = lint_cn_shot("他一边把玩玉佩一边说话。")
    assert "CN_GAZE_ON_PROP" in _codes(hit)
    ok = lint_cn_shot("他一边把玩玉佩一边说话，目光落在对方脸上。")
    assert "CN_GAZE_ON_PROP" not in _codes(ok)


def test_space_event_in_closeup():
    hit = lint_cn_shot("大特写：暴雨砸漏了房顶。")
    assert "CN_SPACE_IN_CLOSEUP" in _codes(hit)
    ok = lint_cn_shot("全景：暴雨砸漏了房顶。")
    assert "CN_SPACE_IN_CLOSEUP" not in _codes(ok)


def test_vague_emotion_needs_face_action():
    hit = lint_cn_shot("他看起来很愤怒。")
    assert "CN_VAGUE_EMOTION" in _codes(hit)
    ok = lint_cn_shot("他很愤怒，双唇抿紧、下颌绷紧、眉压低。")
    assert "CN_VAGUE_EMOTION" not in _codes(ok)


def test_pose_regression_requires_previous_shot():
    # 没有上一镜 → 不判（判据本身依赖前后关系）
    assert "CN_POSE_REGRESSION" not in _codes(lint_cn_shot("她躺着不动。"))
    hit = lint_cn_shot("她躺着不动。", prev_cn_text="她撑起身子。")
    assert "CN_POSE_REGRESSION" in _codes(hit)
    # 写了过程交代 → 合法
    ok = lint_cn_shot("她力竭又躺回床上。", prev_cn_text="她撑起身子。")
    assert "CN_POSE_REGRESSION" not in _codes(ok)


def test_dialogue_budget_uses_02s_per_char():
    # 5s ≈ 25 字预算
    ok = lint_cn_shot("中景。", dialogue="今晚的海面，好像藏着什么。", seconds=5)
    assert "CN_DIALOGUE_TOO_LONG" not in _codes(ok)
    long_line = "这是一句特别长的台词" * 4  # 40 字
    hit = lint_cn_shot("中景。", dialogue=long_line, seconds=5)
    assert "CN_DIALOGUE_TOO_LONG" in _codes(hit)


# ---------------- 英文层 ----------------

def test_good_english_prompt_is_clean():
    assert lint_video_prompt_en(GOOD_EN, seconds=5) == []


def test_missing_head():
    assert "EN_NO_HEAD" in _codes(lint_video_prompt_en("A boy walks. 0-2s: walking."))


def test_missing_timeline():
    p = "5 seconds, 16:9 horizontal. A boy walks. Ambient wind. No text or watermark."
    assert "EN_NO_TIMELINE" in _codes(lint_video_prompt_en(p, seconds=5))


def test_missing_sound():
    p = "5 seconds, 16:9 horizontal. 0-2s: he pokes the fire. 2-5s: smoke rises. No text."
    assert "EN_NO_SOUND" in _codes(lint_video_prompt_en(p, seconds=5))


def test_tail_must_be_exclusion():
    p = ("5 seconds, 16:9 horizontal. 0-2s he walks. 2-5s he stops. "
         "Ambient sounds of wind. Then the camera slowly pulls back over the hill.")
    assert "EN_TAIL_NOT_EXCLUSION" in _codes(lint_video_prompt_en(p, seconds=5))


def test_cjk_residue_ignores_quoted_dialogue():
    # 引号内是台词，允许中文
    ok = '5 seconds, 16:9 horizontal. 0-2s: Chen Xun says: "今晚的海面。" Ambient sounds. No text.'
    assert "EN_CJK_RESIDUE" not in _codes(lint_video_prompt_en(ok, seconds=5))
    # 引号外残留 → 命中（真机出现过 `a mood of panic and绝望`）
    bad = '5 seconds, 16:9 horizontal. 0-2s: a mood of panic and绝望. Ambient sounds. No text.'
    assert "EN_CJK_RESIDUE" in _codes(lint_video_prompt_en(bad, seconds=5))


def test_empty_prompt():
    assert _codes(lint_video_prompt_en("")) == ["EN_EMPTY"]


# 真机回归：2026-09-24 慢测（真 LLM 第 1 轮）产出的正文原样——机检当时返回空列表，
# 漏了句尾那句「as described in the Chinese text below」。这条用例就是给它钉的。
REAL_LEAK = (
    "5 seconds, 16:9 horizontal. A medium close-up of Chen Xun lying on a grassy slope "
    "with a black bull lying beside him in warm morning light, as described in the Chinese "
    "text below. 0-2s: The camera slowly tracks horizontally across the scene, showing Chen "
    "Xun chewing on a foxtail grass stalk. 2-5s: The slow horizontal camera movement continues. "
    'Ambient sounds of the countryside accompany the scene. Chen Xun says: "今晚的海面。" '
    "Deformed hands, extra limbs, on-screen text, watermark must not appear. "
    "Use the given first frame as the literal opening frame; extend it forward with motion. "
    "No extra background music; keep only natural ambient sounds and action sounds."
)


def test_meta_reference_leak_is_caught_on_real_output():
    codes = _codes(lint_video_prompt_en(REAL_LEAK, seconds=5))
    assert codes == ["EN_META_REFERENCE"]
    # 引号内台词仍是合法中文，不该被当成漏翻
    assert "EN_CJK_RESIDUE" not in codes


def test_meta_reference_variants_and_no_false_positive():
    for bad in (
        "0-2s: he walks, as mentioned in the description. Ambient wind. No text.",
        "0-2s: he walks. Nothing may come after this final sentence. Ambient wind. No text.",
        "0-2s: he walks. Translate from the text above. Ambient wind. No text.",
    ):
        assert "EN_META_REFERENCE" in _codes(lint_video_prompt_en(bad, seconds=5)), bad
    # 干净的稿子不能误报
    assert "EN_META_REFERENCE" not in _codes(lint_video_prompt_en(GOOD_EN, seconds=5))


# 真机回归（2026-09-24 第 3 轮）：整段**没有声音描述**，只有末尾那句排除句。
# 旧判据被排除句里的 `sounds` 满足 → 漏报；同时说话人留了中文「陈浔」。
REAL_NO_SOUND = (
    "5 seconds, 16:9 horizontal. A medium close-up of Chen Xun lying on a grassy slope "
    "chewing foxtail grass with his black cow resting nearby, bathed in warm morning light "
    "as the camera slowly pans horizontally across the scene. 0-2s: Chen Xun lies on the "
    "green grass slope chewing foxtail grass. 2-5s: The camera continues its slow horizontal "
    'pan across Chen Xun and the cow. 陈浔 says: "今晚的海面，好像藏着什么。" '
    "Deformed hands, extra limbs, on-screen text, watermark. "
    "Use the given first frame as the literal opening frame; extend it forward with motion. "
    "No extra background music; keep only natural ambient sounds and action sounds."
)


def test_sound_rule_ignores_the_exclusion_clause_on_real_output():
    codes = _codes(lint_video_prompt_en(REAL_NO_SOUND, seconds=5))
    assert "EN_NO_SOUND" in codes          # 真的没写声音（排除句不算）
    assert "EN_CJK_RESIDUE" in codes       # 说话人留了中文


def test_sound_rule_accepts_a_real_sound_description():
    p = ("5 seconds, 16:9 horizontal. 0-2s: he walks. 2-5s: he stops. "
         "Ambient sound of wind through grass. No text or watermark.")
    assert "EN_NO_SOUND" not in _codes(lint_video_prompt_en(p, seconds=5))


# ---------------- 组合 ----------------

def test_lint_segment_merges_both_layers():
    issues = lint_segment(
        cn_text="他愤怒地把玩玉佩。",
        video_prompt_en="A boy walks.",
        dialogue="很短",
        seconds=5,
    )
    codes = _codes(issues)
    assert "CN_VAGUE_EMOTION" in codes
    assert "CN_GAZE_ON_PROP" in codes
    assert "EN_NO_HEAD" in codes


def test_all_rules_are_warn_only():
    issues = lint_segment(cn_text="不发光。面无笑意。", video_prompt_en="", seconds=5)
    assert issues
    assert all(i["level"] == "WARN" for i in issues)
    assert has_fail(issues) is False


def test_issue_shape_is_serializable():
    import json
    issues = lint_segment(cn_text="不发光。", video_prompt_en="x", seconds=5)
    json.dumps(issues)  # 必须能进 segments_json
    for i in issues:
        assert set(i) == {"code", "level", "rule", "msg"}


# ---------------- 接入点（storyboard 真的把结果带出去） ----------------

@pytest.mark.asyncio
async def test_canvas_storyboard_attaches_lint(monkeypatch):
    """画布链路：机检结果必须随段落库出去（否则等于没接）。"""
    from app.nodes import storyboard as sb_mod

    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        return ("5 seconds, 16:9 horizontal. 0-2s: he walks. 2-5s: he stops. "
                "Ambient wind. No text or watermark.")

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)
    state = {
        "session_id": "lint-canvas",
        "segments": [{
            "image_url": "https://example.com/a.png",
            "prompt": "他愤怒地把玩玉佩。",
            "seconds": 5,
        }],
    }
    out = await sb_mod.canvas_storyboarder_node(state)
    shot = out["storyboard"][0]
    codes = [i["code"] for i in shot["prompt_lint"]]
    assert "CN_VAGUE_EMOTION" in codes
    assert "CN_GAZE_ON_PROP" in codes


@pytest.mark.asyncio
async def test_standard_storyboard_attaches_lint(monkeypatch):
    """标准链路同样要带（两条路径共用同一套提交入口）。"""
    from app.nodes import storyboard as sb_mod

    async def fake_chat(prompt, model=None, temperature=None, session_id=None):
        return ("5 seconds, 16:9 horizontal. 0-2s: he walks. 2-5s: he stops. "
                "Ambient wind. No text or watermark.")

    monkeypatch.setattr(sb_mod.gateway, "chat", fake_chat)
    state = {
        "session_id": "lint-standard",
        "script": [{"shot_id": 0, "visual": "他不发光。", "duration": 5}],
    }
    out = await sb_mod.storyboarder_node(state)
    shot = out["storyboard"][0]
    assert [i["code"] for i in shot["prompt_lint"]] == ["CN_BARE_NEGATION"]


# 真机回归（2026-09-24 第 5 轮 · 用户看片后重跑）：模型把模板「End with …」的
# 指令框架词抄进正文 —— `End with: deformed hands, …`。
REAL_END_WITH = (
    "5 seconds, 16:9 horizontal. A medium close-up of Chen Xun lying on a grassy slope "
    "chewing a foxtail grass blade. 0-2s: The camera slowly pans right. 2-5s: Chen Xun says: "
    '"今晚的海面，好像藏着什么。" Ambient sound of a quiet rural pasture with light wind. '
    "End with: deformed hands, extra limbs, on-screen text, watermark must not appear. "
    "No extra background music; keep only natural ambient sounds and action sounds."
)


def test_end_with_instruction_leak_on_real_output():
    codes = _codes(lint_video_prompt_en(REAL_END_WITH, seconds=5))
    assert "EN_META_REFERENCE" in codes


def test_instruction_framing_words_do_not_false_positive():
    # 正常英文里「ends with a smile」不是指令框架词
    p = ("5 seconds, 16:9 horizontal. 0-2s: he walks. 2-5s: the shot ends with a smile. "
         "Ambient wind. No text or watermark.")
    assert "EN_META_REFERENCE" not in _codes(lint_video_prompt_en(p, seconds=5))
