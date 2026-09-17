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
  anchorNamesInPrompt,
  parseAnchorRefs,
  pickUrlsByPrompt,
  serializeAnchorRefs,
  urlMapOf,
  type AnchorMap,
} from './anchors';

const URL_A = 'https://cdn.example.com/a.png';
const URL_B = 'https://cdn.example.com/b.png';

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
    expect(out.endsWith('画面内容：陈浔在破旧山洞里整理草药')).toBe(true);
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
