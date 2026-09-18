/**
 * 「这一段到底带哪几张参考图、每张是第几号（`<Picture N>`）」—— **唯一出处**。
 *
 * ## 为什么单独一个模块（P2-9 的真实缺陷）
 *
 * 提交时每段的 `reference_images` 是按提示词**逐段现算**的（P0-3 每段只带这一段真正用到的锚定图），
 * 而元素绑定面板此前按**全局**编号显示（锚定图从 2 起顺序编号、与实际组装无关）。
 * 结果（2026-09-18 用真实画布 40 v16 实测）：面板写「山村茅屋废墟 = 图片 7」，
 * 而 agent 实际写进提示词的是 `<Picture 4>`；面板写「小黑子 = 图片 4」，
 * 实际这一段根本没带它 —— **6 个段落里逐段全都不一致**。
 * 用户照面板配的编号去手改提示词，就会指向不存在的图 / 指错对象。
 *
 * 修法：把「组装数组」与「算编号」抽成纯函数，**提交路径与面板显示共用同一份**；
 * 编号的判据只有一个 —— `url` 在**这一段真实数组**里的下标 + 1
 * （与 agent 侧 `app/utils/prompting.py:build_reference_bindings` 的现算口径逐字一致）。
 */
import { MAX_REF_PICTURES, pickUrlsByPrompt } from './anchors';

/** 参与组装的段（与提交载荷的字段名一致：`image_url` 是本段自己的首帧图）。 */
export type SegmentForRefs = {
  image_url?: string;
  prompt?: string;
};

/**
 * 这一段**真实**发给模型的参考图数组。
 *
 * 组装规则（与 agent 侧 `canvas_storyboarder_node` 的截断口径一致）：
 *   `[本段自己的首帧图, ...命中的角色锚, ...命中的场景锚]` → 截断 `MAX_REF_PICTURES`
 *
 * 兜底口径也在这里（角色未命中 `'all'`、场景未命中 `'none'`，理由见 `anchors.ts`），
 * 所以**任何**需要知道「第 N 张是谁」的地方都必须走这个函数，别自己拼数组。
 */
export function segmentRefImages(
  seg: SegmentForRefs,
  charUrls: Record<string, string>,
  sceneUrls: Record<string, string>,
  limit = MAX_REF_PICTURES,
): string[] {
  const { picked: chars } = pickUrlsByPrompt(seg.prompt ?? '', charUrls);
  const { picked: scenes } = pickUrlsByPrompt(seg.prompt ?? '', sceneUrls, {
    fallback: 'none',
  });
  const merged = [seg.image_url, ...Object.values(chars), ...Object.values(scenes)].filter(
    (u): u is string => !!u,
  );
  return merged.slice(0, Math.max(0, limit));
}

/** 这张图在**这一段**里的编号（1-based，`<Picture N>` 的 N）；本段没有这张图 → `null`。 */
export function pictureIndexOf(refs: string[], url: string): number | null {
  const i = refs.indexOf(url);
  return i < 0 ? null : i + 1;
}

/**
 * 同一张锚定图在**各段**里的真实编号（按段顺序遍历、去重、升序）。
 *
 * - 空数组 = 没有任何一段带它（提示词没提到它，或被 5 张上限截断）→ 不会随段发给模型；
 * - 一个元素 = 各段编号一致（面板直接显示 `图片 N`）；
 * - 多个元素 = 逐段不同（面板显示 `逐段 ...`，别只显示一个数字去骗用户）。
 */
export function bindingIndexesInSegments(
  url: string,
  segments: SegmentForRefs[],
  charUrls: Record<string, string>,
  sceneUrls: Record<string, string>,
  limit = MAX_REF_PICTURES,
): number[] {
  const seen = new Set<number>();
  for (const seg of segments) {
    const idx = pictureIndexOf(segmentRefImages(seg, charUrls, sceneUrls, limit), url);
    if (idx !== null) seen.add(idx);
  }
  return [...seen].sort((a, b) => a - b);
}

/**
 * 面板徽标文案 + 悬浮说明。**只依赖上面的编号函数**（显示即事实）。
 *
 * `globalIndex` 只在「画布上还没有可提交的段落」时兜底显示 ——
 * 那时没有任何一段可算编号，显示预期值并说明原因，好过显示空白。
 */
export function bindingBadge(
  indexes: number[],
  globalIndex: number,
  hasSegments: boolean,
): { text: string; title: string; overLimit: boolean } {
  if (!hasSegments) {
    return {
      text: `图片 ${globalIndex}`,
      title: `画布上还没有可提交的段落，先按 ${globalIndex} 号预估；提交时按各段实际组装现算`,
      overLimit: globalIndex > MAX_REF_PICTURES,
    };
  }
  if (indexes.length === 0) {
    return {
      text: '未随段发送',
      title:
        '没有任何一段的提示词提到它（或被 5 张上限截断），因此不会随段发给模型 —— ' +
        '把剧本名词填成提示词里出现过的写法即可命中',
      overLimit: false,
    };
  }
  if (indexes.length === 1) {
    return {
      text: `图片 ${indexes[0]}`,
      title: '各片段组装后它的真实位置（<Picture N> 的 N）',
      overLimit: false,
    };
  }
  return {
    text: `逐段 ${indexes.join('/')}`,
    title: `逐段编号不同：${indexes.map((n) => `图片 ${n}`).join('、')} —— 每段只带这一段用到的锚定图，所以同一张图在各段的编号会变（<Picture N> 由 agent 按各段实际数组现算）`,
    overLimit: false,
  };
}
