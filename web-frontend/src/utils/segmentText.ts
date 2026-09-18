/**
 * 段描述取值的**唯一出处**（2026-09-19 修 #29 时抽出）。
 *
 * ## 缺陷
 *
 * agent 侧标准模式（一句话生成）的分镜段只写 `cn_description`，**没有 `prompt`**：
 * `app/nodes/storyboard.py` 组装段时写的是 `"cn_description": cn_description,`
 * 而 Java `getSegments` 把段字典**原样**返回。
 * 前端却按 `seg.prompt` 读（`TaskSegment.prompt` 还声明成必填）⇒
 * 「段重生」面板显示 **「（空提示词，将用默认运镜）」**、编辑框空白 ——
 * 用户以为那段没描述、或者一保存就把描述清空。
 *
 * 实测（只读接口，任务 54）：
 * `GET /api/tasks/54/segments` → 段字段 = [aspect_ratio, camera_spec, **cn_description**,
 * existing_video_url, index, mode, negative_prompt, pending_video_id, prompt_en,
 * reference_images, seconds, shot_id, style_prompt, thumbnail] —— **无 `prompt`**，
 * 而 `cn_description` 里有完整描述文本。
 *
 * 取值的优先级：画布/图片任务是 `prompt`（用户自己写的），标准模式是 `cn_description`
 * （LLM 分镜产物）；两者语义都是"这一段要画什么"，所以按顺序取第一个非空的。
 */
export interface SegmentLike {
  prompt?: string;
  /** 标准模式（一句话生成）的段描述；画布模式没有这个字段 */
  cn_description?: string;
}

/** 取该段的展示/编辑用描述：`prompt` 优先，回落 `cn_description`，都没有才空串。
 *
 *  逐项 trim 后再判空 —— `'   '` 是 truthy，用 `a || b` 会让空白串把回落挡掉
 *  （本函数的单测当场抓到过这个边界）。 */
export function segmentPromptText(seg: SegmentLike | null | undefined): string {
  if (!seg) {
    return '';
  }
  const own = (seg.prompt || '').trim();
  if (own) {
    return own;
  }
  return (seg.cn_description || '').trim();
}
