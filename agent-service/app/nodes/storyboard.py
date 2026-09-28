"""LangGraph 节点：storyboarder（分镜 → 英文提示词 + 生成参数）。

可灵式精细控制：
- 风格提示词 / 负面提示词 折进提示词正文（agnes 无独立字段，实测未知字段 400）
- 结构化运镜（景别/机位/运镜）翻译为确定性英文片段，追加到 prompt_en
- 元素语义绑定 [{name, image_index}] → <Picture N> 占位符（agnes reference 模式官方支持）

Phase 4 P0：mode 和 reference_images 初始为空，由后续 image_generator 节点回填。
"""
import logging

from app.config import settings
from app.gateway.agnes import gateway
from app.nodes.script import _coerce_int
from app.state import CreativeSessionState, TaskStatus
from app.utils.prompting import (
    FIRST_FRAME_EN,
    build_cn_description,
    build_reference_bindings,
    build_video_rewrite_input,
    camera_phrase,
    normalize_camera_spec,
    sound_clause,
)

logger = logging.getLogger(__name__)

# Agnes 视频时长合法范围：4~12 秒（实测 API 返回 "seconds must be in [4, 12]"）
MIN_SECONDS = 4
MAX_SECONDS = 12
# 标准模式画幅（画布模式取每段自己的 aspect_ratio）
STANDARD_ASPECT_RATIO = "16:9"

TRANSLATE_TEMPLATE = (
    "Translate the following Chinese video description to an English video-generation "
    "prompt. Output only the English prompt, no explanation.\n\n{text}"
)


async def translate_to_en(text: str) -> str:
    """中文 → 英文提示词（**唯一的 LLM 出口**，两条链路都从这里走）。

    ⚠️ 视频链路（`video_prompt_from`）刻意复用本函数，而不是另开一个 LLM 出口：
    测试只要 patch 这一处就能拦下 storyboard 的全部 LLM 调用（见
    tests/test_first_frame_and_ratio.py 的 `_fake_translate`）。
    """
    resp = await gateway.chat(
        TRANSLATE_TEMPLATE.format(text=text),
        model=settings.text_model,
        temperature=0.1,
    )
    return resp.strip()


async def video_prompt_from(
    cn_description: str, seconds: object, aspect_ratio: object, camera_en: str = "",
    dialogue: str = "", speaker: str = "",
) -> str:
    """中文分镜描述 → 英文**视频**提示词（Agnes Video 2.5 规范）。

    ★ 2026-09-24：此前视频提交的是「图像提示词整段翻译」—— 实测 payload 里
      没有时间轴、没有任何声音指令（而产物 ffprobe 全带 aac 音轨，即 BGM 由模型
      自由发挥、段段不同）。这里按官方规范（时长+画幅头 / 核心创意 / 时间轴分段 /
      声音段 / 一镜到底）改写一次。

    `camera_en` 注入模板内部（不事后追加）：实测事后追加会得到
    「…watermark., medium shot, slow push-in」——运镜重复、且把排除句挤出末位。
    `dialogue`/`speaker` 同理由模板注入：台词**逐字**要求（文档速查表第 2 条），
    经 LLM 翻译会被改写，所以放在指令里明令 "quote verbatim" 而不是混进中文描述。
    """
    return await translate_to_en(
        build_video_rewrite_input(cn_description, seconds, aspect_ratio, camera_en,
                                  dialogue=dialogue, speaker=speaker))


def _dialogue_of(src: dict) -> tuple[str, str]:
    """取本镜的台词与说话人（兼容 snake / camel 两种键名）。"""
    text = str(src.get("dialogue") or src.get("dialogueText") or "").strip()
    who = str(src.get("dialogue_speaker") or src.get("dialogueSpeaker") or "").strip()
    return text, who


def _custom_video_prompt(src: dict) -> str:
    """用户在画布节点上写的**视频提示词**（空 = 没自定义，走「本段描述」改写）。

    ★ 2026-09-24：画布节点新增该编辑框 —— 此前视频提示词是每次提交时现生成的，
      用户既看不到也改不了（`data.prompt` 存的是图像提示词）。
    """
    return str(src.get("video_prompt_cn") or src.get("videoPromptCn") or "").strip()


def _decorate_prompt(
    base: str,
    role: list[str] | tuple[str, ...] = (),
    keep: list[str] | tuple[str, ...] = (),
    camera_en: str = "",
    sound: str = "",
) -> str:
    """给提示词补**确定性**后缀：元素绑定 → 运镜 → 声音段（顺序固定、逐字拼接）。

    ⚠️ 一律不经 LLM：`<Picture N>` 绑定、运镜术语、声音排除句被模型改写就失去意义
    （排除句尤其如此 —— 文档要求它是个明确的反向约束）。
    """
    out = str(base or "").strip()
    if role:
        out = f"{role[0]} {out}"
    if keep:
        out = f"{out} {keep[0]}"
    if camera_en:
        out = f"{out}, {camera_en}"
    if sound:
        out = f"{out} {sound}"
    return out


def _control_context(state: CreativeSessionState) -> tuple[str, str, list[str], list[str]]:
    """取出全局精细控制参数：风格、负面词、元素绑定提示句。"""
    style_prompt = str(state.get("style_prompt") or "").strip()
    negative_prompt = str(state.get("negative_prompt") or "").strip()
    role_clauses, keep_clauses = build_reference_bindings(state.get("reference_bindings") or [])
    return style_prompt, negative_prompt, role_clauses, keep_clauses


async def storyboarder_node(state: CreativeSessionState) -> dict:
    from app import events

    # ⚠️ 这个节点原先**一个事件都不发**（只有 `canvas_storyboarder` 发）——
    # 标准模式主链路上「分镜拆解」整步在实时视图里是消失的（而 trace 快照有）。
    # entered 必须在幂等守卫之前发：断点恢复时本节点确实被走到过。
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "storyboarder", "node_name": "分镜拆解"})
    # 幂等守卫（断点恢复）：storyboard 非空且每镜都有 prompt_en → 跳过 LLM 翻译，
    # 直接沿用已有 storyboard（含 reference_images / 复用字段）
    existing_sb = state.get("storyboard") or []
    if existing_sb and all(str(s.get("prompt_en") or "").strip() for s in existing_sb):
        logger.info("storyboarder 幂等跳过：已有 %d 镜 prompt_en", len(existing_sb))
        await events.emit(state["session_id"], "node_completed",
                          {"node_id": "storyboarder",
                           "summary": f"复用已有 {len(existing_sb)} 镜分镜"})
        return {}
    # 用户上传的参考图（图生视频模式）：有则作为每镜参考图，空则后续 image_generator 自动生图回填
    user_ref_images = list(state.get("reference_images", []))
    style_prompt, negative_prompt, role_clauses, keep_clauses = _control_context(state)
    # 全局运镜倾向（③-1）：用户指定后覆盖 LLM 每镜自由发挥的 camera，
    # 保证「同等控制力」——不给则保持 LLM 自由分镜（行为不变）。
    global_camera_spec = normalize_camera_spec(state.get("shot_language") or {})
    global_camera_en = camera_phrase(global_camera_spec)
    storyboard = []
    for shot in state["script"]:
        # 有全局运镜时用它替换 LLM 的 camera，避免两种运镜指令互相冲突
        # （中文描述用中文原值，确定性英文片段在翻译后拼接）
        camera_text = (
            "、".join(v for v in global_camera_spec.values() if v)
            if global_camera_en else shot.get("camera", "")
        )
        cn_description = build_cn_description(
            [shot.get("visual", ""), camera_text, shot.get("style_note", "")],
            style_prompt=style_prompt,
            negative_prompt=negative_prompt,
        )
        # 时长钳制到 [4, 12]：分镜可能给 2-3s 短镜，但 Agnes 下限是 4s
        raw_seconds = _coerce_int(shot.get("duration")) or 5
        seconds = max(MIN_SECONDS, min(raw_seconds, MAX_SECONDS))
        # 图像/参考提示词（纯翻译）
        en_prompt = await translate_to_en(cn_description)
        # ★ 2026-09-24：视频规范提示词单开一个字段 —— 本节点产出的 `prompt_en` 还要
        #   喂 `image_generator` 出图（nodes/image.py:396），把时间轴/声音塞进去对图像
        #   模型只是噪音。视频提示词只在 video_generator 里消费（见 nodes/video.py）。
        _dlg_text, _dlg_who = _dialogue_of(shot)
        video_en = await video_prompt_from(cn_description, seconds=seconds,
                                           aspect_ratio=STANDARD_ASPECT_RATIO,
                                           camera_en=global_camera_en,
                                           dialogue=_dlg_text, speaker=_dlg_who)
        # 元素语义绑定：<Picture N> 角色定义放句首（agnes 官方推荐显式点名每个占位符）
        # 确定性英文运镜片段：翻译之后再拼，保证术语精确
        en_prompt = _decorate_prompt(en_prompt, role_clauses, keep_clauses, global_camera_en)
        # 视频那条**不再事后追加运镜**（已注入改写模板内部，见 video_prompt_from）；
        # 声音段必须是最后一句（文档：末尾约束权重最高）
        video_en = _decorate_prompt(video_en, role_clauses, keep_clauses,
                                    sound=sound_clause(bool(state.get("bgm"))))
        # mode 和 reference_images 的填充规则：
        # - 用户传了参考图 → 用用户图，走 mode="reference"（agnès 参考模式）
        # - 否则留空，由 image_generator 节点自动生图回填
        storyboard.append({
            "shot_id": shot.get("shot_id", len(storyboard)),
            "prompt_en": en_prompt,
            "video_prompt_en": video_en,
            # 台词随段落库（原文 + 说话人）：Java 落库后可展示/编辑，
            # 段重生时原样复用（描述一改由 SegmentReworkPlanner 一并清掉）
            "dialogue": _dlg_text,
            "dialogue_speaker": _dlg_who,
            "mode": "reference" if user_ref_images else "text",
            "seconds": str(seconds),
            "aspect_ratio": STANDARD_ASPECT_RATIO,
            "reference_images": list(user_ref_images),
            "cn_description": cn_description,
            # 精细控制参数随段落库（段重生时原样复用）
            "camera_spec": global_camera_spec,
            "style_prompt": style_prompt,
            "negative_prompt": negative_prompt,
        })
    await events.emit(state["session_id"], "node_completed",
                      {"node_id": "storyboarder", "summary": f"{len(storyboard)} 镜分镜"})
    return {"storyboard": storyboard, "status": TaskStatus.STORYBOARD_WRITING}


async def canvas_storyboarder_node(state: CreativeSessionState) -> dict:
    """无限画布模式：用户自定片段列表 → storyboard（每段一镜，图生视频）。

    每个片段 = 一张参考图 + 一段视频内容描述（+ 可选时长 + 可选结构化运镜），
    直接翻译为英文提示词，跳过剧本/分镜 LLM 生成环节——用户自己就是导演。
    """
    from app import events
    await events.emit(state["session_id"], "node_entered",
                      {"node_id": "canvas_storyboarder", "node_name": "画布分镜"})

    segments = list(state.get("segments", []))
    # 元素绑定的声明句**在下面逐段现算**（<Picture N> 必须对照这一段真实的参考图数组，
    # 否则某段没有自己的首帧图时数组前移 → 绑错对象；详见 utils/prompting.py 的说明）。
    # 这里只要风格与负面词。
    style_prompt, negative_prompt, _global_role, _global_keep = _control_context(state)
    bindings = state.get("reference_bindings") or []
    storyboard = []
    for idx, seg in enumerate(segments):
        # 本段自己的首帧图（画布图片节点的产物）—— keyframe 模式用它锁首帧
        own_image = str(seg.get("image_url", "")).strip()
        # 多参考图：优先读前端透传的 reference_images 数组
        # （用户源图 + 角色锚定图 + 场景锚定图），兼容旧 image_url 单张。
        # agnes reference 模式硬限制 5 张。
        ref_images = list(seg.get("reference_images") or [])
        if not ref_images and own_image:
            ref_images.append(own_image)
        if len(ref_images) > 5:
            ref_images = ref_images[:5]
        cn = str(seg.get("prompt", "")).strip()
        # 描述为空时给默认动作，避免空提示词
        if not cn:
            cn = "对参考图内容做缓慢推进的动态运镜"
        # ★ 2026-09-24：本镜台词（原文 + 说话人）—— 画布节点/段配置里带来，
        #   注入改写模板让正式提示词逐字带上（文档速查表第 2 条「台词 = 原文」）
        _dlg_text, _dlg_who = _dialogue_of(seg)
        # 段级负面词覆盖全局
        seg_negative = str(seg.get("negative_prompt") or "").strip() or negative_prompt
        camera_spec = normalize_camera_spec(seg.get("camera_spec"))
        camera_en = camera_phrase(camera_spec)
        # 段重生时 storyboard 已带 prompt_en / video_prompt_en，直接复用（跳过 LLM，省额度）
        raw_seconds = _coerce_int(seg.get("seconds")) or 5
        seconds = max(MIN_SECONDS, min(raw_seconds, MAX_SECONDS))
        ratio = str(seg.get("aspect_ratio") or STANDARD_ASPECT_RATIO).strip() or STANDARD_ASPECT_RATIO
        en_prompt = str(seg.get("prompt_en", "")).strip()
        video_en = str(seg.get("video_prompt_en", "")).strip()
        if not en_prompt or not video_en:
            # 图像/参考提示词固定来自「本段描述」（+ 全局风格/段级负面词）
            img_cn = build_cn_description([cn], style_prompt=style_prompt,
                                          negative_prompt=seg_negative)
            if not en_prompt:
                en_prompt = await translate_to_en(img_cn)
            if not video_en:
                # ★ 2026-09-24：视频提示词按 Agnes Video 2.5 规范改写（时长+画幅头 /
                #   时间轴分段 / 声音段）—— 画布链路没有出图环节，这条正文就是提交给
                #   agnes 的那条；此前它是「图像提示词整段翻译」，没有时间轴与声音。
                #   用户在节点上自定义了视频提示词时以它为准（不再叠风格/负面词 ——
                #   那两样在自定义正文里通常已经有了；图像那条不受影响）。
                video_cn = _custom_video_prompt(seg) or img_cn
                video_en = await video_prompt_from(video_cn, seconds=seconds,
                                                   aspect_ratio=ratio, camera_en=camera_en,
                                                   dialogue=_dlg_text, speaker=_dlg_who)
            # 元素绑定：按**这一段真实的参考图数组**现算 <Picture N>（见 prompting.py）
            # 声音段：确定性后缀（已排除运镜 —— 它注入了改写模板内部），
            # BGM 开关关闭时明确排除背景音乐，且必须是最后一句
            seg_role, seg_keep = build_reference_bindings(bindings, ref_images)
            en_prompt = _decorate_prompt(en_prompt, seg_role, seg_keep, camera_en)
            video_en = _decorate_prompt(video_en, seg_role, seg_keep,
                                        sound=sound_clause(bool(state.get("bgm"))))
        storyboard.append({
            "shot_id": idx,
            "prompt_en": en_prompt,
            "video_prompt_en": video_en,
            # 台词随段落库（段重生时原样复用；Java 的 SegmentReworkPlanner 会按
            # 「勾选重生」成对清掉派生译文，这两个原文字段跟着描述一起留/清）
            "dialogue": _dlg_text,
            "dialogue_speaker": _dlg_who,
            # 用户自定义的视频提示词（原文输入，空串 = 跟随本段描述）
            "video_prompt_cn": _custom_video_prompt(seg),
            "mode": "reference" if ref_images else "text",
            "seconds": str(seconds),
            "aspect_ratio": ratio,
            "reference_images": ref_images,
            # 本段首帧图（后面按 lock_first_frame 决定它当 first_frame 还是只当参考图）
            "first_frame": own_image,
            "cn_description": cn,
            # 重生模式：已有视频 URL → 直接复用，跳过 agnes 重新生成
            "existing_video_url": str(seg.get("existing_video_url") or "").strip(),
            # 断点恢复：进程重启前已提交 agnes 且仍在生成的原 video_id
            # （恢复流程写入 segments；正常链路无此字段）
            "pending_video_id": str(seg.get("pending_video_id") or "").strip(),
            # 精细控制参数随段落库
            "camera_spec": camera_spec,
            "style_prompt": style_prompt,
            "negative_prompt": seg_negative,
        })

    # === 首帧锁定 / 段间衔接（2026-09-18）===
    #
    # 背景：此前把本段首帧图塞进 `reference_images`（mode=reference）。官方对 reference
    # 的定义是「当作内容/风格/运动参考，**可能重新构图、重新计时**」—— 也就是说用户
    # 认可的这张首帧图**不是**视频的起点，用户看到的「视频和我出的图不像」是预期行为。
    # keyframe 模式的定义才是「尝试把输入图作为实际第一帧」。
    #
    # ⚠️ 官方约束：keyframe 与 reference **互斥**（keyframe 不许带 images），所以
    #   「锁定首帧」和「锚定图参考」不能同时生效。默认选前者，理由：本段首帧图本身
    #   就是按该段提示词（含角色/场景描述）生成的，已承载该段的角色形象与场景；
    #   而跨段一致性由每段各自的、已被用户认可的首帧图承担。
    #   要回到旧行为：请求里 lock_first_frame=false（前端「首帧锁定」开关关掉）。
    lock = bool(state.get("lock_first_frame", True))
    chain = bool(state.get("chain_frames", False))
    locked = 0
    for idx, sb_shot in enumerate(storyboard):
        first = str(sb_shot.get("first_frame") or "").strip()
        if lock and first:
            sb_shot["mode"] = "keyframe"
            sb_shot["first_frame"] = first
            # ★ 2026-09-19 修（#25）：**keyframe 会丢掉参考图，必须同时清掉悬空的 <Picture N> 声明**。
            #   元素绑定（`<Picture N>`）是给 reference 模式用的；而官方约束 keyframe 与 reference
            #   互斥（keyframe 不许带 images）⇒ 参考图被网关丢弃，提示词里却还留着
            #   "Picture 2 is X ..." 这种**指向不存在图片**的指令：模型可能理解成
            #   「画面里要有这些元素」而画出不该有的对象，用户也会觉得"绑定明明配了却没效果"且查不出原因。
            #   这里按**这一段真实的参考图数组**重算同一批子句并**逐字删除**（精确匹配，不用正则；
            #   子句文本由 build_reference_bindings 现算，与注入时同一来源）。
            #   设计上本就不指望绑定在锁定首帧时生效 —— 跨段一致性由每段已被用户认可的首帧图承担。
            _seg_refs = list(sb_shot.get("reference_images") or [])
            if _seg_refs:
                _role, _keep = build_reference_bindings(bindings, _seg_refs)
                _dead = [str(c).strip() for c in (*_role, *_keep) if str(c).strip()]
                # ★ 2026-09-24：两个提示词字段都要清 —— `video_prompt_en` 里同样带着
                #   这批指向不存在图片的悬空引用（它才是实际提交给 agnes 的那条）。
                for _field in ("prompt_en", "video_prompt_en"):
                    _text = str(sb_shot.get(_field) or "")
                    if not _text:
                        continue
                    for _clause in _dead:
                        _text = _text.replace(_clause, "")
                    sb_shot[_field] = " ".join(_text.split())
            # ★ 2026-09-24：keyframe（只给首帧）按官方语义是「只补首帧之后的动作/光影/声音」，
            #   补一句确定性说明，免得模型按 reference 的理解重构图、重计时。
            if sb_shot.get("video_prompt_en"):
                sb_shot["video_prompt_en"] = f"{sb_shot['video_prompt_en']} {FIRST_FRAME_EN}"
            # 段间衔接：下一段的首帧当本段尾帧（last_frame）——让相邻段首尾接得上。
            # 默认关：强制结尾构图会压住本段的运动，不是所有题材都想要。
            if chain and idx + 1 < len(storyboard):
                nxt = str(storyboard[idx + 1].get("first_frame") or "").strip()
                if nxt and nxt != first:
                    sb_shot["last_frame"] = nxt
            locked += 1
        else:
            # 没锁首帧时不要留 first_frame：reference 模式下网关会打日志丢弃它
            sb_shot.pop("first_frame", None)
            sb_shot["mode"] = "reference" if sb_shot.get("reference_images") else "text"

    if locked:
        await events.emit(state["session_id"], "progress",
                          {"phase": f"首帧锁定：{locked}/{len(storyboard)} 段以 keyframe 模式生成"})
    await events.emit(state["session_id"], "node_completed",
                      {"node_id": "canvas_storyboarder", "summary": f"画布分镜 {len(storyboard)} 段"})
    return {"storyboard": storyboard, "status": TaskStatus.STORYBOARD_WRITING}
