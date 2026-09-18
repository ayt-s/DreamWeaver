import { describe, expect, it } from 'vitest';

import { filmSecondsFromChain } from './canvasPlan';

/**
 * 画布「每段时长」必须真的生效（2026-09-19 修 #21 的回归测试）。
 *
 * 缺陷：`plan` 边遍历边取值，而成片节点在 chain 里排在图片节点之后
 * ⇒ 每段 push 时拿到的还是初值 4，用户设置 6 秒完全无效，而顶栏显示 6 秒。
 * 实测复刻脚本：`1 图 + 成片 6s → seconds: 4`；`3 图串联 + 成片 6s → [4,4,4]`。
 */
describe('filmSecondsFromChain', () => {
  const img = (id: string) => ({ id, type: 'imageNode', data: {} });
  const video = (id: string, seconds: number) => ({ id, type: 'videoNode', data: { seconds } });

  it('成片节点排在图片节点之后时，仍取到成片的秒数（这就是原来的 bug）', () => {
    const nodes = [img('n1'), img('n2'), video('v1', 6)];
    expect(filmSecondsFromChain(['n1', 'n2', 'v1'], nodes)).toBe(6);
  });

  it('成片节点排在前面时同样取到', () => {
    const nodes = [video('v1', 8), img('n1')];
    expect(filmSecondsFromChain(['v1', 'n1'], nodes)).toBe(8);
  });

  it('没有成片节点时回落默认 4 秒', () => {
    expect(filmSecondsFromChain(['n1'], [img('n1')])).toBe(4);
  });

  it('多个成片节点时取最后一个（与顶栏显示口径一致）', () => {
    const nodes = [video('v1', 5), img('n1'), video('v2', 9)];
    expect(filmSecondsFromChain(['v1', 'n1', 'v2'], nodes)).toBe(9);
  });

  it('成片节点秒数为 0/缺省时回落 4 秒', () => {
    expect(filmSecondsFromChain(['v1'], [{ id: 'v1', type: 'videoNode', data: {} }])).toBe(4);
  });
});
