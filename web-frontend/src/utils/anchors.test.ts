/**
 * 锚定图读写与用法（P0-1 / P0-3 的纯逻辑）。
 *
 * 重点锁三件：
 * 1. **向后兼容**：旧的 `{名字: url}` 必须能读；没有描述时必须写回旧格式
 *    （否则存量画布会被无谓改写，旧前端/旧脚本读不了）；
 * 2. **匹配口径唯一**：首帧描述注入与每段参考图筛选必须用同一套名字匹配；
 * 3. **不劣化**：一个名字都没匹配上时要退回「全给」——与改动前行为一致。
 */
import { describe, expect, it } from 'vitest';

import {
  augmentPromptWithAnchors,
  anchorsForPrompt,
  anchorKeyMatches,
  anchorNamesInPrompt,
  aliasOf,
  CLAUSE_COVERAGE_MIN,
  clauseCoverage,
  clausesOf,
  COSTUME_MAX_CHARS,
  COSTUME_RED_LINE,
  costumeOf,
  parseAnchorRefs,
  pickUrlsByPrompt,
  serializeAnchorRefs,
  urlMapOf,
  type AnchorMap,
} from './anchors';

const URL_A = 'https://cdn.example.com/a.png';
const URL_B = 'https://cdn.example.com/b.png';
const URL_C = 'https://cdn.example.com/c.png';

/** 真实数据里的场景锚 key：存的是**生成锚定图那段描述原文**（画布项目 40 的形状）。 */
const SCENE_KEY =
  '山村茅屋废墟，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助';
/** 分镜 `[场景]` 段：与上面的 key 只差两字（茅屋废墟 → 茅草屋）。 */
const SCENE_PROMPT =
  '[场景] 山村茅草屋，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助';
/** 角色卡（含服装 / 发型分句，用来验改动三的造型锁定）。 */
const CHAR_DESC =
  '17岁少年，男性，身形偏瘦但结实，黑色短发略显凌乱，五官轮廓分明，肤色偏白，常穿粗布短衫和补丁裤子，腰间系麻绳，脚踩草鞋';

describe('parseAnchorRefs', () => {
  it('读得懂旧格式（纯字符串值）', () => {
    expect(parseAnchorRefs('{"陈浔":"https://x/a.png"}')).toEqual({
      陈浔: { url: 'https://x/a.png' },
    });
  });

  it('读得懂新格式（带描述）', () => {
    expect(parseAnchorRefs('{"陈浔":{"url":"https://x/a.png","desc":"青年男性"}}')).toEqual({
      陈浔: { url: 'https://x/a.png', desc: '青年男性' },
    });
  });

  it('脏数据一律安全返回空表，不抛异常', () => {
    for (const raw of ['', null, undefined, 'not-json', '[]', '"str"', '{}']) {
      expect(parseAnchorRefs(raw as string)).toEqual({});
    }
  });

  it('丢掉空 url / 空名字，保留合法项', () => {
    expect(parseAnchorRefs('{"  ":"u", "甲":"", "乙":"  https://x/b.png  "}')).toEqual({
      乙: { url: 'https://x/b.png' },
    });
  });
});

describe('parseAnchorRefs 接受已解析对象（URL ?anchorRefs 传的是对象，不是 JSON 串）', () => {
  it('旧的纯 url 与新的 {url, desc} 都吃', () => {
    expect(parseAnchorRefs({ 陈浔: 'https://x/a.png' })).toEqual({
      陈浔: { url: 'https://x/a.png' },
    });
    expect(parseAnchorRefs({ 陈浔: { url: 'https://x/a.png', desc: '青年' } })).toEqual({
      陈浔: { url: 'https://x/a.png', desc: '青年' },
    });
  });
});

describe('urlMapOf（纯 url 视图：提交载荷与面板缩略图用）', () => {
  it('把 {url, desc} 压回 url', () => {
    expect(urlMapOf({ 甲: { url: URL_A, desc: '描述' }, 乙: { url: URL_B } })).toEqual({
      甲: URL_A,
      乙: URL_B,
    });
  });
});

describe('serializeAnchorRefs', () => {
  it('★ 没有描述时写回旧格式（不让存量数据变形）', () => {
    expect(serializeAnchorRefs({ 陈浔: { url: URL_A } })).toBe('{"陈浔":"' + URL_A + '"}');
  });

  it('有描述时写新格式', () => {
    const s = serializeAnchorRefs({ 陈浔: { url: URL_A, desc: '青年男性' } });
    expect(JSON.parse(s as string)).toEqual({ 陈浔: { url: URL_A, desc: '青年男性' } });
  });

  it('空表 → undefined（调用方据此不发这个字段）', () => {
    expect(serializeAnchorRefs({})).toBeUndefined();
  });

  it('新旧格式往返一致', () => {
    const mixed: AnchorMap = { 甲: { url: URL_A, desc: '描述' }, 乙: { url: URL_B } };
    expect(parseAnchorRefs(serializeAnchorRefs(mixed))).toEqual(mixed);
  });
});

describe('anchorNamesInPrompt', () => {
  const refs: AnchorMap = { 陈浔: { url: URL_A }, 破旧山洞: { url: URL_B } };

  it('按名字包含判断命中', () => {
    expect(anchorNamesInPrompt('陈浔在破旧山洞口整理收获', refs)).toEqual(['陈浔', '破旧山洞']);
    expect(anchorNamesInPrompt('陈浔独自走在山道上', refs)).toEqual(['陈浔']);
  });

  it('没提到就返回空（调用方据此决定兜底）', () => {
    expect(anchorNamesInPrompt('一只猫在窗台打盹', refs)).toEqual([]);
    expect(anchorNamesInPrompt('', refs)).toEqual([]);
  });
});

describe('anchorsForPrompt（每段只带相关锚定图）', () => {
  const chars: AnchorMap = { 陈浔: { url: URL_A }, 大黑牛: { url: URL_B } };
  const scenes: AnchorMap = { 破旧山洞: { url: URL_B } };

  it('只留下这段真正用到的', () => {
    const r = anchorsForPrompt('陈浔走进破旧山洞', chars, scenes);
    expect(Object.keys(r.chars)).toEqual(['陈浔']);
    expect(Object.keys(r.scenes)).toEqual(['破旧山洞']);
    expect(r.matched).toBe(true);
  });

  it('★ 一个都没匹配到 → 退回全给（与改动前一致，不劣化）', () => {
    const r = anchorsForPrompt('一只猫在窗台打盹', chars, scenes);
    expect(r.matched).toBe(false);
    expect(Object.keys(r.chars).sort()).toEqual(['大黑牛', '陈浔']);
    expect(Object.keys(r.scenes)).toEqual(['破旧山洞']);
  });
});

describe('pickUrlsByPrompt（每段筛选的老结构入口，P0-3）', () => {
  const chars = { 陈浔: URL_A, 大黑牛: URL_B };

  it('只留下这段真正提到的名字', () => {
    expect(pickUrlsByPrompt('陈浔牵着大黑牛走进山洞', chars)).toEqual({
      picked: chars,
      matched: true,
    });
    expect(pickUrlsByPrompt('陈浔独自站在崖边', chars)).toEqual({
      picked: { 陈浔: URL_A },
      matched: true,
    });
  });

  it('★ 一个都没提到 → 全给 + matched=false（与改动前一致，不劣化）', () => {
    expect(pickUrlsByPrompt('一只猫在窗台打盹', chars)).toEqual({
      picked: chars,
      matched: false,
    });
  });

  it('空映射/空提示词都不崩', () => {
    expect(pickUrlsByPrompt('随便什么', {})).toEqual({ picked: {}, matched: false });
    expect(pickUrlsByPrompt('', chars)).toEqual({ picked: chars, matched: false });
  });
});

describe('augmentPromptWithAnchors（首帧文生图带角色描述）', () => {
  const chars: AnchorMap = { 陈浔: { url: URL_A, desc: '二十岁青年男性，粗布短衫，腰间开山斧' } };
  const scenes: AnchorMap = { 破旧山洞: { url: URL_B, desc: '潮湿岩洞，苔藓覆盖' } };

  it('★ 命中且带描述 → 拼在画面内容之前', () => {
    const out = augmentPromptWithAnchors('陈浔在破旧山洞里整理草药', chars, scenes);
    expect(out).toContain('陈浔：二十岁青年男性');
    expect(out).toContain('破旧山洞：潮湿岩洞');
    // ⚠️ 这里**刻意不用 endsWith**：改动三把造型红线追加到了整段末尾
    //（模型对末尾约束更敏感，红线必须落在末尾），所以「画面内容：…」不再收尾。
    expect(out).toContain('画面内容：陈浔在破旧山洞里整理草药');
  });

  it('没有描述（老数据）→ 原样返回，行为不变', () => {
    const bare: AnchorMap = { 陈浔: { url: URL_A } };
    expect(augmentPromptWithAnchors('陈浔在洞口', bare, {})).toBe('陈浔在洞口');
  });

  it('没提到任何锚定图 → 不拼（别把全片角色设定塞进无关镜头）', () => {
    expect(augmentPromptWithAnchors('一只猫在窗台打盹', chars, scenes)).toBe('一只猫在窗台打盹');
  });

  it('超长描述被截断（防提示词被设定撑爆）', () => {
    const long: AnchorMap = { 甲: { url: URL_A, desc: 'x'.repeat(300) } };
    const out = augmentPromptWithAnchors('甲站在那里', long, {}, 20);
    expect(out).toContain('x'.repeat(20));
    expect(out).not.toContain('x'.repeat(21));
  });
});

describe('anchorKeyMatches（改动一：长 key 降级匹配）', () => {
  it('★ 长 key（场景描述原文）与 [场景] 段只差两字 → 分句覆盖率命中', () => {
    expect(anchorKeyMatches(SCENE_KEY, SCENE_PROMPT)).toBe(true);
    // 命中靠的是**整段描述的分句重合度**（7/8 条分句逐字重合），不是整串包含、
    // 也不是「首分句像」——后者会把同地点另一时段的那张也判成命中（见下一条）。
    expect(clausesOf(SCENE_KEY)[0]).toBe('山村茅屋废墟');
    expect(clauseCoverage(SCENE_PROMPT, SCENE_KEY)).toBeGreaterThanOrEqual(
      CLAUSE_COVERAGE_MIN,
    );
  });

  it('★★ 同地点、另一时段的那张（山村茅草屋·午后）**不命中** —— 首分句 LCS 口径会误判', () => {
    // 真实数据（画布项目 40 第 4 镜）抓到的假阳性：段里写「山村茅草屋，黄昏，余烬未熄…」，
    // 若按「首个分句相似度」，锚定图「山村茅草屋，午后，灶火正旺…」的首分句逐字命中 → 1.0
    // → 该镜同时带上「山村茅屋废墟」与这张，背景被往「午后完好的屋子」拉。
    // 分句覆盖率把它压到 2/8 = 0.25，低于阈值 → 只保留正确那张。
    expect(
      anchorKeyMatches(
        SCENE_KEY,
        '[场景] 山村茅草屋，午后，灶火正旺，炊烟袅袅升起，屋内土墙斑驳，木桌简陋，地面铺干草，弥漫着饭香，气氛温暖质朴',
      ),
    ).toBe(false);
  });

  it('★ 同一 key 对「村外山洞·日间」不命中', () => {
    expect(
      anchorKeyMatches(SCENE_KEY, '[场景] 村外山洞，日间，洞内灰褐色石壁，地面铺满干草'),
    ).toBe(false);
  });

  it('★ 同一 key 对「小山村山坡·清晨」不命中', () => {
    expect(anchorKeyMatches(SCENE_KEY, '[场景] 小山村山坡，清晨，阳光斜照，绿意盎然')).toBe(false);
  });

  it('短 key（人名 / 物名）行为完全不变：纯包含判断', () => {
    expect(anchorKeyMatches('陈浔', '陈浔在破旧山洞里整理草药')).toBe(true);
    expect(anchorKeyMatches('陈浔', '一只猫在窗台打盹')).toBe(false);
    expect(anchorKeyMatches('破旧山洞', '陈浔走进破旧山洞')).toBe(true);
    expect(anchorKeyMatches('', '随便什么')).toBe(false);
    expect(anchorKeyMatches('陈浔', '')).toBe(false);
  });

  it('碎片 key（分句都很短、只重合一两条）→ 覆盖率不够，不命中', () => {
    const key = '山村，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外';
    expect(clausesOf(key)).toEqual([
      '山村',
      '黄昏',
      '余烬未熄',
      '黑烟仍在上升',
      '焦黑的木梁倒在地上',
      '一人一牛跪坐门外',
    ]);
    // 「山村」「黄昏」命中 = 2/6 ≈ 0.33 < 0.5
    expect(anchorKeyMatches(key, '[场景] 山村茅草屋，黄昏')).toBe(false);
  });

  it('没命中就没有兜底 —— 兜底仍是调用方的事（matched=false 时全给）', () => {
    const scenes: AnchorMap = { 小山村山坡: { url: URL_A }, [SCENE_KEY]: { url: URL_C } };
    const miss = anchorsForPrompt('一只猫在窗台打盹', {}, scenes);
    expect(miss.matched).toBe(false);
    expect(Object.keys(miss.scenes).sort()).toEqual(['小山村山坡', SCENE_KEY].sort());
  });
});

describe('别名口径（改动二：末 2 字只对短 key 生效）', () => {
  const refs: AnchorMap = { 大黑牛: { url: URL_A }, 陈浔: { url: URL_B } };

  it('★ 短 key 认「末 2 字」简称（与 agent 侧 _char_aliases 同口径）', () => {
    expect(aliasOf('大黑牛')).toBe('黑牛');
    expect(anchorNamesInPrompt('黑牛反刍保下些许大米', refs)).toEqual(['大黑牛']);
  });

  it('没提到的仍然一个都不给', () => {
    expect(anchorNamesInPrompt('一只猫在窗台打盹', refs)).toEqual([]);
  });

  it('★ 头号陷阱：长 key（场景描述）绝不用末 2 字 —— 含「无助」的文本不得命中该场景锚', () => {
    expect(SCENE_KEY.endsWith('氛围凄凉无助')).toBe(true);
    expect(aliasOf(SCENE_KEY)).toBe(SCENE_KEY); // 长 key 没有别名
    expect(anchorKeyMatches(SCENE_KEY, '他感到无助')).toBe(false);
    expect(anchorNamesInPrompt('他感到无助，望着远处的山', { [SCENE_KEY]: { url: URL_A } })).toEqual(
      [],
    );
  });

  it('2 字以内的 key 不产生额外别名（末 2 字就是它自己）', () => {
    expect(aliasOf('陈浔')).toBe('陈浔');
    expect(anchorKeyMatches('陈浔', '浔独自走着')).toBe(false);
  });
});

describe('长 key 场景锚落地（缺口一的真实症状：别再触发「全给」兜底）', () => {
  const scenes: AnchorMap = { 小山村山坡: { url: URL_A }, [SCENE_KEY]: { url: URL_C } };

  it('★ 差两字的 [场景] 段命中正确的那张，且不带上无关场景（新结构入口）', () => {
    const r = anchorsForPrompt(SCENE_PROMPT, {}, scenes);
    expect(r.matched).toBe(true);
    expect(Object.keys(r.scenes)).toEqual([SCENE_KEY]);
  });

  it('★ 老结构入口（pickUrlsByPrompt）同一口径 → 不再全给', () => {
    const urls = { 小山村山坡: URL_A, [SCENE_KEY]: URL_C };
    expect(pickUrlsByPrompt(SCENE_PROMPT, urls)).toEqual({
      picked: { [SCENE_KEY]: URL_C },
      matched: true,
    });
  });
});

describe('costumeOf（改动三：造型分句提取，抠不到就空串 —— 不编）', () => {
  it('★ 只收含线索词的分句，用「，」连接', () => {
    const c = costumeOf(CHAR_DESC);
    expect(c).toContain('黑色短发略显凌乱');
    expect(c).toContain('常穿粗布短衫和补丁裤子');
    expect(c).not.toContain('17岁少年'); // 无线索词的分句不进造型短语
    expect(c).not.toContain('五官轮廓分明');
    expect(c).not.toContain('腰间系麻绳');
  });

  it('★ 造型短语硬上限 24 字', () => {
    const long = '常穿赭色粗布短衫，下身青布裤子，脚踩草鞋，头戴黑色布带，腰间系红绳';
    expect(costumeOf(long)).toBe(long.slice(0, COSTUME_MAX_CHARS));
    expect(costumeOf(long).length).toBe(COSTUME_MAX_CHARS);
  });

  it('★ 抠不到线索词 → 空串（不编）', () => {
    expect(costumeOf('一株老松树，树干皲裂')).toBe('');
    expect(costumeOf('潮湿岩洞，苔藓覆盖')).toBe('');
    expect(costumeOf('')).toBe('');
  });
});

describe('augmentPromptWithAnchors 造型锁定（改动三，与 agent 侧对齐）', () => {
  it('★ 抠到造型 → 条目末尾追加造型短语，且逐字红线落在整段末尾', () => {
    const chars: AnchorMap = { 陈浔: { url: URL_A, desc: CHAR_DESC } };
    const out = augmentPromptWithAnchors('陈浔在山坡上躺坐', chars, {});
    expect(out).toContain('常穿粗布短衫和补丁裤子');
    expect(out).toContain('（全片服装发型保持一致：');
    expect(out.endsWith(COSTUME_RED_LINE)).toBe(true);
    expect(COSTUME_RED_LINE).toBe('同一角色在各镜头中的服装与发型必须完全一致，不得换装');
  });

  it('★ 一条造型都没抠到 → 输出与改动前逐字一致（无追加、无红线）', () => {
    const noClue: AnchorMap = { 老松: { url: URL_A, desc: '一株老松树，树干皲裂' } };
    expect(augmentPromptWithAnchors('老松立在崖边', noClue, {})).toBe(
      '角色与场景设定（保持与下述描述一致）：老松：一株老松树，树干皲裂。画面内容：老松立在崖边',
    );
  });

  it('造型短语超 24 字 → 截断（不让提示词被重复设定撑爆）', () => {
    const long: AnchorMap = {
      甲: { url: URL_A, desc: '常穿赭色粗布短衫，下身青布裤子，脚踩草鞋，头戴黑色布带' },
    };
    const out = augmentPromptWithAnchors('甲站在那里', long, {});
    const m = out.match(/（全片服装发型保持一致：([^）]*)）/);
    expect(m).not.toBeNull();
    const costume = (m as RegExpMatchArray)[1];
    expect(costume.length).toBe(COSTUME_MAX_CHARS);
    expect(costume.startsWith('常穿赭色粗布短衫，下身青布裤子')).toBe(true);
  });

  it('多个命中项都有造型 → 红线只在末尾出现一次', () => {
    const two: AnchorMap = {
      陈浔: { url: URL_A, desc: CHAR_DESC },
      王二: { url: URL_B, desc: '中年男子，穿灰布长袍，束发戴布带' },
    };
    const out = augmentPromptWithAnchors('陈浔与王二在洞口', two, {});
    expect(out.split(COSTUME_RED_LINE).length - 1).toBe(1);
    expect(out.endsWith(COSTUME_RED_LINE)).toBe(true);
    expect(out).toContain('王二：中年男子，穿灰布长袍，束发戴布带（全片服装发型保持一致：');
  });

  it('maxDescChars 截断行为不变（造型追加不改变设定本身的限长）', () => {
    const long: AnchorMap = { 甲: { url: URL_A, desc: `${'甲'.repeat(30)}，常穿粗布短衫` } };
    const out = augmentPromptWithAnchors('甲站在那里', long, {}, 10);
    expect(out).toContain('甲：' + '甲'.repeat(10));
    expect(out).not.toContain('甲'.repeat(11));
    expect(out).toContain('常穿粗布短衫'); // 造型来自完整描述，仍被强调
  });
});

describe('未命中兜底策略（fallback）', () => {
  const chars = { 陈浔: URL_A };
  const scenes = { 破旧山洞: URL_B };

  it('默认 all：整类全给（老行为，向后兼容）', () => {
    expect(pickUrlsByPrompt('一只猫在窗台打盹', scenes)).toEqual({ picked: scenes, matched: false });
  });

  it('★ fallback=none：不给（场景侧的生产用法 —— 给错场景比不给更糟）', () => {
    expect(pickUrlsByPrompt('一只猫在窗台打盹', scenes, { fallback: 'none' })).toEqual({
      picked: {},
      matched: false,
    });
  });

  it('命中时 fallback 不起作用（照给命中的那些）', () => {
    expect(pickUrlsByPrompt('陈浔走进破旧山洞', scenes, { fallback: 'none' })).toEqual({
      picked: scenes,
      matched: true,
    });
  });

  it('anchorsForPrompt 支持按类给策略（生产：角色 all / 场景 none）', () => {
    const r = anchorsForPrompt(
      '一只猫在窗台打盹',
      { 陈浔: { url: URL_A } },
      { 破旧山洞: { url: URL_B } },
      { charFallback: 'all', sceneFallback: 'none' },
    );
    expect(r.matched).toBe(false);
    expect(Object.keys(r.chars)).toEqual(['陈浔']);
    expect(Object.keys(r.scenes)).toEqual([]);
    expect(chars['陈浔']).toBe(URL_A); // 防止变量未使用告警
  });
});
