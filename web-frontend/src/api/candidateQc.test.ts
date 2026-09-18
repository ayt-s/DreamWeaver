/**
 * 候选主体计数：**相对多数**判定（`outliersBySubjects`）。
 *
 * 这条判定的语义是「哪张跟别张数出来不一样」——**不是**「哪张错了」。
 * 三张一起错（模型侧随机）时它给不出线索，宁可返回空集也不假装有结论。
 */
import { describe, expect, it } from 'vitest';

import { MAX_CANDIDATE_QC, outliersBySubjects, type CandidateSubjects } from './candidateQc';

function wrap(results: Array<{ url: string; label: string; skipped?: boolean }>): CandidateSubjects {
  const full = results.map((r, i) => ({
    index: i,
    url: r.url,
    skipped: r.skipped ?? false,
    people: 1,
    animals: 1,
    faceCloseup: false,
    label: r.label,
  }));
  return { results: full, summary: { total: full.length, counted: full.filter((r) => !r.skipped).length } };
}

describe('outliersBySubjects', () => {
  it('★ 2:1 → 只标出少数那张（「三张里有一张两头牛」的形态）', () => {
    const data = wrap([
      { url: 'a.png', label: '1人1牛' },
      { url: 'b.png', label: '1人1牛' },
      { url: 'c.png', label: '1人2牛' },
    ]);
    expect([...outliersBySubjects(data)]).toEqual(['c.png']);
  });

  it('全一样 → 空集（三张一起错的形态，相对比较给不出线索）', () => {
    const data = wrap([
      { url: 'a.png', label: '1人2牛' },
      { url: 'b.png', label: '1人2牛' },
      { url: 'c.png', label: '1人2牛' },
    ]);
    expect(outliersBySubjects(data).size).toBe(0);
  });

  it('skipped 的既不算多数派、也不被标出（数不出来就不猜）', () => {
    const data = wrap([
      { url: 'a.png', label: '1人1牛' },
      { url: 'b.png', label: '' , skipped: true },
      { url: 'c.png', label: '2人1牛' },
    ]);
    // 有效只剩 2 张且互不相同 → 各占一票，「多数派」取排序第一，另一张被标出
    const out = outliersBySubjects(data);
    expect(out.has('b.png')).toBe(false);
    expect(out.size).toBe(1);
  });

  it('只有 1 张有效 → 空集（没有可比较的对象）', () => {
    expect(outliersBySubjects(wrap([{ url: 'a.png', label: '1人1牛' }])).size).toBe(0);
  });

  it('null / 空 results 都安全', () => {
    expect(outliersBySubjects(null).size).toBe(0);
    expect(outliersBySubjects(wrap([])).size).toBe(0);
  });

  it('上限常量与 agent 侧一致（超了接口 422）', () => {
    expect(MAX_CANDIDATE_QC).toBe(6);
  });
});
