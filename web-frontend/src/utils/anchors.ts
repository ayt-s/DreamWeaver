/**
 * 锚定图（角色 / 场景一致性参考）的读写与用法。
 *
 * ## 为什么要单独一个模块
 *
 * `canvas_project.character_refs / scene_refs` 里存的一直是 `{名字: url}` 的 JSON。
 * 但**生成锚定图时用的那段描述从来没被保存过** —— 于是有两个连带病：
 *
 * 1. **重新生成锚定图**时只能让用户重打描述（默认给的是「名字」，出图很随机）；
 * 2. **首帧图（文生图）拿不到角色描述**，只能靠分镜描述里偶然提到的特征
 *    —— 这是「跳脸 / 换背景」的根因之一（锚定图只在视频阶段当参考图用，
 *    而首帧才是画面的真正基底；agnes 图片接口又不接受图片输入，只能靠文字）。
 *
 * 所以这里把值升级为 `{url, desc?}`：**读时兼容旧的纯字符串**，
 * **写时没描述就写回纯字符串**（不让存量数据无谓变形，新旧前端都能读）。
 */
export type AnchorRef = { url: string; desc?: string };
export type AnchorMap = Record<string, AnchorRef>;

/** agnes reference 模式硬上限：images 数组最多 5 张（与 agent 侧截断一致）。 */
export const MAX_REF_PICTURES = 5;

/**
 * 解析锚定图。容忍三种历史形态：`{n: "url"}` / `{n: {url, desc}}` / 脏数据。
 *
 * `raw` 既可以是 JSON 字符串（`canvas_project.character_refs` 的存储形态），
 * 也可以是**已解析的对象**（NovelPage 转画布时塞在 URL 里的 `?anchorRefs=` 参数）。
 */
export function parseAnchorRefs(
  raw?: string | Record<string, unknown> | null,
): AnchorMap {
  if (!raw) return {};
  let obj: unknown;
  if (typeof raw === 'string') {
    try {
      obj = JSON.parse(raw);
    } catch {
      return {};
    }
  } else {
    obj = raw;
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {};

  const out: AnchorMap = {};
  for (const [name, value] of Object.entries(obj as Record<string, unknown>)) {
    const key = String(name).trim();
    if (!key) continue;
    if (typeof value === 'string') {
      const url = value.trim();
      if (url) out[key] = { url };
      continue;
    }
    if (value && typeof value === 'object') {
      const v = value as { url?: unknown; desc?: unknown };
      const url = String(v.url ?? '').trim();
      if (!url) continue;
      const desc = String(v.desc ?? '').trim();
      out[key] = desc ? { url, desc } : { url };
    }
  }
  return out;
}

/** 序列化锚定图。**全都没有描述时写回旧的纯字符串格式**（兼容旧读取方）。 */
export function serializeAnchorRefs(refs: AnchorMap): string | undefined {
  const names = Object.keys(refs);
  if (names.length === 0) return undefined;
  // ⚠️ 只要**有任意一项**带描述就整份走新格式；否则没描述的那些会被漏掉
  // （第一版就是这么写的：hasDesc 时只输出 rich，plain 里的条目全丢 —— 往返测试当场抓到）
  const hasDesc = names.some((n) => !!refs[n].desc?.trim());
  const out: Record<string, string | AnchorRef> = {};
  for (const name of names) {
    const ref = refs[name];
    const desc = ref.desc?.trim();
    if (!hasDesc) {
      out[name] = ref.url;
    } else {
      out[name] = desc ? { url: ref.url, desc } : { url: ref.url };
    }
  }
  return JSON.stringify(out);
}

/** 「名字 → url」的纯 url 视图（老结构的消费者用：提交载荷、面板缩略图）。 */
export function urlMapOf(refs: AnchorMap): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, ref] of Object.entries(refs)) out[name] = ref.url;
  return out;
}

/**
 * 段落提示词里提到的锚定图名字。
 *
 * 用于两处（口径必须一致，否则「首帧加了谁」与「参考图给了谁」会不一致）：
 * 1. 每段该带哪些锚定图（无关角色不再被硬塞进画面、也不再挤占 5 张名额）；
 * 2. 首帧文生图该把谁的描述拼进提示词。
 *
 * 规则就是朴素包含判断：名字是用户自己填的（「陈浔」「破旧山洞」），
 * 中文没有词边界，包含判断足够；**一个都没匹配到时由调用方决定兜底行为**。
 */
export function anchorNamesInPrompt(prompt: string, refs: Record<string, unknown>): string[] {
  const text = String(prompt ?? '');
  if (!text.trim()) return [];
  return Object.keys(refs).filter((name) => name.trim() && text.includes(name.trim()));
}

/**
 * 从「名字 → url」映射里挑出这段提示词提到的项（P0-3 的每段筛选）。
 *
 * 与 `anchorsForPrompt` 是**同一套匹配口径**的两个入口：那边面向 `{url, desc}` 的新结构，
 * 这边面向画布当前还在用的「名字 → url」老结构（接线完成前两者并存，口径必须一致）。
 * 一个都没匹配到 → `matched=false`，由调用方决定兜底（当前是「全给」，与改动前一致）。
 */
export function pickUrlsByPrompt(
  prompt: string,
  urls: Record<string, string>,
): { picked: Record<string, string>; matched: boolean } {
  const hits = anchorNamesInPrompt(prompt, urls);
  if (hits.length === 0) return { picked: urls, matched: false };
  const picked: Record<string, string> = {};
  for (const name of hits) if (urls[name]) picked[name] = urls[name];
  return { picked, matched: true };
}

/** 每段实际要带上的锚定图（命中不了就**全给**，保持与改动前一致＝不劣化）。 */
export function anchorsForPrompt(
  prompt: string,
  charRefs: AnchorMap,
  sceneRefs: AnchorMap,
): { chars: AnchorMap; scenes: AnchorMap; matched: boolean } {
  const charHits = anchorNamesInPrompt(prompt, charRefs);
  const sceneHits = anchorNamesInPrompt(prompt, sceneRefs);
  const matched = charHits.length + sceneHits.length > 0;
  if (!matched) return { chars: charRefs, scenes: sceneRefs, matched: false };
  return {
    chars: pick(charRefs, charHits),
    scenes: pick(sceneRefs, sceneHits),
    matched: true,
  };
}

function pick(refs: AnchorMap, names: string[]): AnchorMap {
  const out: AnchorMap = {};
  for (const name of names) if (refs[name]) out[name] = refs[name];
  return out;
}

/**
 * 把命中的锚定图**描述**拼到首帧文生图的提示词前。
 *
 * 只在描述存在时拼；没有描述（老数据）就原样返回，行为与改动前一致。
 * 前缀写成「角色设定：…」这种显式句式，避免被模型当成画面内容的一部分。
 */
export function augmentPromptWithAnchors(
  prompt: string,
  charRefs: AnchorMap,
  sceneRefs: AnchorMap,
  maxDescChars = 120,
): string {
  const base = String(prompt ?? '').trim();
  const hits = [
    ...anchorNamesInPrompt(base, charRefs).map((n) => [n, charRefs[n]] as const),
    ...anchorNamesInPrompt(base, sceneRefs).map((n) => [n, sceneRefs[n]] as const),
  ].filter(([, ref]) => !!ref.desc?.trim());
  if (hits.length === 0) return base;

  const lines = hits.map(([name, ref]) => {
    const desc = (ref.desc ?? '').trim().slice(0, maxDescChars);
    return `${name}：${desc}`;
  });
  return `角色与场景设定（保持与下述描述一致）：${lines.join('；')}。画面内容：${base}`;
}
