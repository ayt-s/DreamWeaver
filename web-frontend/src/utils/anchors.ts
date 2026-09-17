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

/** 长 key 的门槛：`key.length > LONG_KEY_MIN` 才算「描述型 key」（人名极少超过 6 字）。 */
const LONG_KEY_MIN = 6;
/** 长 key 降级判据：分句覆盖率阈值（半数以上分句逐字重合才算「同一个场景」）。 */
export const CLAUSE_COVERAGE_MIN = 0.5;

/**
 * 按 `[，,。；;、]` 切分，取所有非空分句并 trim（长 key 的比对单位）。
 *
 * 分句 = 场景描述里的一个事实（「黄昏」「余烬未熄」「一人一牛跪坐门外」），
 * 比「整串包含」松、比「首分句近似」准 —— 判据见 `clauseCoverage`。
 */
export function clausesOf(key: string): string[] {
  return String(key ?? '')
    .split(/[，,。；;、]/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/**
 * 分句覆盖率（0~1）：key 的分句里有多少条**逐字出现**在 text 里。
 *
 * ★ 为什么不沿用「首个分句 + 滑窗 LCS」：首分句只能说明开头像，分不出
 *   「同一场戏的废墟版」和「同一地点、另一时段」这两张锚定图。
 *   真实数据（画布项目 40 第 4 镜，2026-09-18）：`[场景]` 写的是
 *   「山村茅草屋，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，
 *   神情呆滞绝望，氛围凄凉无助」，它与
 *   - 「山村茅屋废墟，黄昏，余烬未熄…」→ 8 条分句里 **7 条**逐字重合（0.88，同一场戏的废墟版）；
 *   - 「山村茅草屋，午后，灶火正旺…」→ 只有 2 条重合（0.25，同地点／另一时段）。
 *   而首分句 LCS 会把后者判成 1.0（「山村茅草屋」逐字命中）→ 该镜带上两张场景锚、
 *   背景被往「午后完好的屋子」拉。改用覆盖率后只剩正确那张。
 */
export function clauseCoverage(text: string, key: string): number {
  const body = String(text ?? '');
  const clauses = clausesOf(key);
  if (!body || clauses.length === 0) return 0;
  let hit = 0;
  for (const c of clauses) if (c.length >= 2 && body.includes(c)) hit += 1;
  return hit / clauses.length;
}

/**
 * key 的等价写法（判定用，**调用方拿到的仍是原始 key**）。
 *
 * 与 agent 侧 `composer.py:_char_aliases` 同口径，但**加了长度闸门**：
 * 只有短 key（≤ 6 字）才认「末 2 字」。
 * ⚠️ 长 key（场景描述）用末 2 字是灾难：「…氛围凄凉无助」的「无助」会让任何
 * 含「无助」的文本误命中该场景锚。长度 ≤ 2 时末 2 字就是原键，等价于没有别名。
 */
export function aliasOf(key: string): string {
  const name = String(key ?? '').trim();
  return name.length > 2 && name.length <= LONG_KEY_MIN ? name.slice(-2) : name;
}

/**
 * 单个锚定图 key 是否出现在这段文本里 —— **锚定图匹配的唯一口径**。
 *
 * 两条入口（`anchorNamesInPrompt` 与它下游的 `pickUrlsByPrompt` /
 * `anchorsForPrompt` / `augmentPromptWithAnchors`）都走这里，口径必须唯一，
 * 否则「首帧加了谁」与「参考图给了谁」会不一致。
 */
export function anchorKeyMatches(key: string, text: string): boolean {
  const name = String(key ?? '').trim();
  const body = String(text ?? '');
  if (!name || !body) return false;
  // 1) 整串包含：长短 key 通用，也是唯一「零回归」的那条路。
  if (body.includes(name)) return true;

  if (name.length > LONG_KEY_MIN) {
    // 2) 长 key 降级：分句覆盖率（整段描述是否真的对得上，而非只是开头像）
    return clauseCoverage(body, name) >= CLAUSE_COVERAGE_MIN;
  }

  // 3) 短 key 别名（末 2 字）。长 key 永远走不到这里。
  const alias = aliasOf(name);
  return alias !== name && body.includes(alias);
}

/**
 * 段落提示词里提到的锚定图名字。
 *
 * 用于两处（口径必须一致，否则「首帧加了谁」与「参考图给了谁」会不一致）：
 * 1. 每段该带哪些锚定图（无关角色不再被硬塞进画面、也不再挤占 5 张名额）；
 * 2. 首帧文生图该把谁的描述拼进提示词。
 *
 * ## 匹配口径（唯一实现：`anchorKeyMatches`）
 *
 * 1. **短 key（≤ 6 字，人名 / 物名 / 短地名）**：整串包含判断 + 末 2 字简称。
 *    末 2 字与 agent 侧 `composer.py:_char_aliases` 同口径 ——
 *    分镜不会总写全名（「大黑牛」常写成「黑牛反刍保下些许大米」）。
 * 2. **长 key（> 6 字，场景描述原文）**：整串包含 → 否则按**分句覆盖率 ≥ 0.5**
 *    判定（key 的一半以上分句逐字出现在文本里）。场景锚的 key 存的是生成锚定图时的
 *    **描述原文**（几十字），而分镜的 `[场景]` 段往往只差一两个字
 *    （「山村茅屋废墟」vs「山村草屋」）→ 整串包含必然失配 → 触发调用方的
 *    「全给」兜底 → 无关场景锚被塞进这一段、正确的反被挤掉。
 *    ⚠️ 长 key **绝不用末 2 字**：「…氛围凄凉无助」的「无助」会让任何含这个词的
 *    文本误命中该场景锚（测试里钉了这条反例）。
 *
 * **一个 key 都没匹配到时由调用方决定兜底行为**（当前是「全给」）。
 */
export function anchorNamesInPrompt(prompt: string, refs: Record<string, unknown>): string[] {
  const text = String(prompt ?? '');
  if (!text.trim()) return [];
  return Object.keys(refs).filter((name) => name.trim() && anchorKeyMatches(name, text));
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

/** 角色描述里「服装 / 发型」的线索词（与 agent 侧 `composer.py:_COSTUME_HINTS` 逐项一致）。 */
export const COSTUME_HINTS = [
  '穿', '服装', '衣', '袍', '裤', '裙', '衫', '褂', '披', '斗笠', '帽', '靴', '鞋',
  '腰带', '束发', '发髻', '长发', '短发', '发冠', '布带',
] as const;

/** 抠出来的造型短语上限（与 agent 侧 `_COSTUME_MAX` 一致）。 */
export const COSTUME_MAX_CHARS = 24;

/**
 * 造型红线（与 agent 侧 `_IMAGE_RED_LINES` 里那条**逐字一致**）。
 * 落在整段提示词**末尾** —— 模型对末尾约束更敏感，这是唯一能同时约束
 * 所有镜头的落点（agent 侧 2026-09-17 实测跨批次造型漂移后加的）。
 */
export const COSTUME_RED_LINE = '同一角色在各镜头中的服装与发型必须完全一致，不得换装';

/**
 * 从描述里抠出「服装 / 发型」的分句（`，` 连接，截断到 24 字）。
 *
 * **抠不到返回空串 —— 不编**（老数据 / 非人角色卡本来就没有造型信息）。
 * 只抠分句、不整卡重复：角色卡那串并列分句本身就会让模型把并列项当成两个主体。
 */
export function costumeOf(desc: string): string {
  const parts = String(desc ?? '').split(/[，。；、]/);
  const hits = parts
    .map((p) => p.trim())
    .filter((p) => !!p && COSTUME_HINTS.some((h) => p.includes(h)));
  if (hits.length === 0) return '';
  return hits.join('，').slice(0, COSTUME_MAX_CHARS);
}

/**
 * 把命中的锚定图**描述**拼到首帧文生图的提示词前。
 *
 * 只在描述存在时拼；没有描述（老数据）就原样返回，行为与改动前一致。
 * 前缀写成「角色设定：…」这种显式句式，避免被模型当成画面内容的一部分。
 *
 * 改动三（造型锁定，与 agent 侧对齐）：条目里能抠出服装/发型分句时，在该条末尾
 * 追加「（全片服装发型保持一致：…）」并把逐字红线落在**整段末尾**；一条都抠不到
 * 就不加红线、输出与改动前**逐字一致**（不编）。
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

  const lines: string[] = [];
  let costumeAdded = false;
  for (const [name, ref] of hits) {
    const desc = (ref.desc ?? '').trim().slice(0, maxDescChars);
    // 造型分句从**完整描述**里抠（与 agent 侧对整张角色卡 `_costume_of` 同口径）：
    // maxDescChars 截断是给「设定整体」限长的，造型只有 ≤24 字，且正是要强调的部分。
    const costume = costumeOf((ref.desc ?? '').trim());
    if (!costume) {
      lines.push(`${name}：${desc}`);
      continue;
    }
    costumeAdded = true;
    lines.push(`${name}：${desc}（全片服装发型保持一致：${costume}）`);
  }
  const body = `角色与场景设定（保持与下述描述一致）：${lines.join('；')}。画面内容：${base}`;
  // 一条造型都没抠到 → 逐字与改动前一致（不编、不加红线）。
  // 有人抠到 → 红线落在**整段末尾**（模型对末尾更敏感，与 agent 侧 `_IMAGE_RED_LINES` 同一落点）。
  return costumeAdded ? `${body}。${COSTUME_RED_LINE}` : body;
}
