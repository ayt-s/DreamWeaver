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
    build_cn_description,
    build_reference_bindings,
    camera_phrase,
    normalize_camera_spec,
)

logger = logging.getLogger(__name__)

# Agnes 视频时长合法范围：4~12 秒（实测 API 返回 "seconds must be in [4, 12]"）
MIN_SECONDS = 4
MAX_SECONDS = 12

TRANSLATE_TEMPLATE = (
    "Translate the following Chinese video description to an English video-generation "
    "prompt. Output only the English prompt, no explanation.\n\n{text}"
)


async def translate_to_en(text: str) -> str:
    resp = await gateway.chat(
        TRANSLATE_TEMPLATE.format(text=text),
        model=settings.text_model,
        temperature=0.1,
    )
    return resp.strip()


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
        en_prompt = await translate_to_en(cn_description)
        # 元素语义绑定：<Picture N> 角色定义放句首（agnes 官方推荐显式点名每个占位符）
        if role_clauses:
            en_prompt = f"{role_clauses[0]} {en_prompt}"
        if keep_clauses:
            en_prompt = f"{en_prompt} {keep_clauses[0]}"
        # 确定性英文运镜片段（翻译之后再拼，保证术语精确）
        if global_camera_en:
            en_prompt = f"{en_prompt}, {global_camera_en}"
        # 时长钳制到 [4, 12]：分镜可能给 2-3s 短镜，但 Agnes 下限是 4s
        raw_seconds = _coerce_int(shot.get("duration")) or 5
        seconds = max(MIN_SECONDS, min(raw_seconds, MAX_SECONDS))
        # mode 和 reference_images 的填充规则：
        # - 用户传了参考图 → 用用户图，走 mode="reference"（agnès 参考模式）
        # - 否则留空，由 image_generator 节点自动生图回填
        storyboard.append({
            "shot_id": shot.get("shot_id", len(storyboard)),
            "prompt_en": en_prompt,
            "mode": "reference" if user_ref_images else "text",
            "seconds": str(seconds),
            "aspect_ratio": "16:9",
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
        # 段级负面词覆盖全局
        seg_negative = str(seg.get("negative_prompt") or "").strip() or negative_prompt
        camera_spec = normalize_camera_spec(seg.get("camera_spec"))
        camera_en = camera_phrase(camera_spec)
        # 段重生时 storyboard 已带 prompt_en，直接复用（跳过 LLM 翻译，省额度）
        en_prompt = str(seg.get("prompt_en", "")).strip()
        if not en_prompt:
            cn_description = build_cn_description([cn], style_prompt=style_prompt,
                                                  negative_prompt=seg_negative)
            en_prompt = await translate_to_en(cn_description)
            # 元素绑定：按**这一段真实的参考图数组**现算 <Picture N>（见 prompting.py）
            seg_role, seg_keep = build_reference_bindings(bindings, ref_images)
            if seg_role:
                en_prompt = f"{seg_role[0]} {en_prompt}"
            if seg_keep:
                en_prompt = f"{en_prompt} {seg_keep[0]}"
            if camera_en:
                en_prompt = f"{en_prompt}, {camera_en}"
        raw_seconds = _coerce_int(seg.get("seconds")) or 5
        seconds = max(MIN_SECONDS, min(raw_seconds, MAX_SECONDS))
        ratio = str(seg.get("aspect_ratio") or "16:9").strip() or "16:9"
        storyboard.append({
            "shot_id": idx,
            "prompt_en": en_prompt,
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
