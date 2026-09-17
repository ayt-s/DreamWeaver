/**
 * 分镜顺序规则（成片顺序 = 图片节点从左到右的 x 坐标）。
 *
 * 这类错误**不会抛异常**，只会让成片顺序不对 —— 所以用例的重点是「顺序对不对」
 * 和「边界点了有没有反应」，而不是有没有崩。
 */
import { describe, expect, it } from 'vitest';

import { SHOT_GAP_X, reorderShotX, sortShots } from './shotOrder';

const shots = (...xs: number[]) => xs.map((x, i) => ({ id: `n${i}`, x, y: 60 }));

/** 把 `节点id → 新 x` 应用回 shots，再按 x 排出新顺序（模拟画布上看到的结果）。 */
function applyAndSort(list: ReturnType<typeof shots>, xs: Map<string, number> | null) {
  if (!xs) return list.map((s) => s.id); // null = 不动
  return list
    .map((s) => ({ ...s, x: xs.get(s.id) ?? s.x }))
    .sort((a, b) => a.x - b.x)
    .map((s) => s.id);
}

describe('sortShots', () => {
  it('按 x 排序', () => {
    expect(sortShots(shots(740, 60, 400)).map((s) => s.id)).toEqual(['n1', 'n2', 'n0']);
  });

  it('x 相同时按 y 兜底（与后端 _pos 的 (x, y) 口径一致）', () => {
    const list = [
      { id: 'a', x: 60, y: 300 },
      { id: 'b', x: 60, y: 60 },
    ];
    expect(sortShots(list).map((s) => s.id)).toEqual(['b', 'a']);
  });
});

describe('reorderShotX', () => {
  it('中间节点上移一位：与前面的邻居换位', () => {
    const list = shots(60, 400, 740);
    expect(applyAndSort(list, reorderShotX(list, 1, -1))).toEqual(['n1', 'n0', 'n2']);
  });

  it('中间节点下移一位', () => {
    const list = shots(60, 400, 740);
    expect(applyAndSort(list, reorderShotX(list, 1, 1))).toEqual(['n0', 'n2', 'n1']);
  });

  it('★ 间距不齐也能正确换位（只交换两个 x 的写法在这里会失效）', () => {
    const list = shots(60, 62, 740); // 前两个几乎重叠
    expect(applyAndSort(list, reorderShotX(list, 0, 1))).toEqual(['n1', 'n0', 'n2']);
  });

  it('★ 两个节点 x 完全相同时也不「点了没反应」', () => {
    const list = shots(60, 60, 740);
    const xs = reorderShotX(list, 0, 1);
    expect(xs).not.toBeNull();
    // n0/n1 的 x 被拆开成两个不同的值（不再重叠，顺序可判）
    expect(xs!.get('n0')).not.toBe(xs!.get('n1'));
  });

  it('归一化成 base + k*步距（与后端 reorder_shots 同一口径）', () => {
    const list = shots(60, 400, 740);
    const ok = reorderShotX(list, 0, 1)!;
    expect([...ok.entries()]).toEqual([
      ['n1', 60],
      ['n0', 60 + SHOT_GAP_X],
      ['n2', 60 + 2 * SHOT_GAP_X],
    ]);
  });

  it('base 取原最小 x：不整体平移（用户摆过的位置尽量别动）', () => {
    const list = shots(200, 540);
    const xs = reorderShotX(list, 0, 1)!;
    expect(Math.min(...xs.values())).toBe(200);
  });

  it('边界：第一个再上移 / 最后一个再下移 → null（不动）', () => {
    const list = shots(60, 400, 740);
    expect(reorderShotX(list, 0, -1)).toBeNull();
    expect(reorderShotX(list, 2, 1)).toBeNull();
  });

  it('边界：只有一个分镜时两个方向都是 null', () => {
    const list = shots(60);
    expect(reorderShotX(list, 0, -1)).toBeNull();
    expect(reorderShotX(list, 0, 1)).toBeNull();
  });

  it('空数组不崩', () => {
    expect(reorderShotX([], 0, 1)).toBeNull();
  });
});
