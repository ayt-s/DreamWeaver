/**
 * 分镜顺序（画布上「第 N 段」的真实来源）。
 *
 * ## 唯一的规则：成片顺序 = 图片节点**从左到右的 x 坐标**
 *
 * 这条规则在三处必须一致，任何一处改了口径都会导致「画布显示的段号」与
 * 「实际成片顺序」不符：
 * - 前端 chain 构建（本文件 + ImageVideoPage 的顺序徽标）
 * - 后端 agent 工具 `reorder_shots` / `add_image_node`（`app/agent/tools.py`）
 * - 后端提交时按顺序取图
 *
 * ## 为什么抽成纯函数
 *
 * 排序错了**不会报错**，只会让成片顺序不对 —— 这类 bug 只能靠测试抓。
 * 组件里的 `moveShot` 只剩「读节点 → 调这里 → 写回」三步。
 */

/** 相邻分镜的 x 步距。**必须与 `app/agent/tools.py` 的 340 一致**（add_image_node/reorder_shots）。 */
export const SHOT_GAP_X = 340;

export type ShotRef = { id: string; x: number; y: number };

/** 按成片顺序排序：先 x，x 相同再按 y（与后端 `_pos` 的 (x, y) 口径一致）。 */
export function sortShots<T extends ShotRef>(shots: T[]): T[] {
  return [...shots].sort((a, b) => a.x - b.x || a.y - b.y);
}

/**
 * 把第 `index` 个分镜 上移/下移 一位，返回 `节点 id → 新 x`。
 *
 * - 越界（第 1 个再上移、最后一个再下移、只有一个节点）返回 `null`，调用方据此不动。
 * - 返回的是**全部**分镜的新 x（归一化成 `base + k * SHOT_GAP_X`），与后端
 *   `reorder_shots` 的做法一致：只交换两个节点的 x 时，间距不齐或两节点 x 相同
 *   就会出现「点了没反应」。
 * - `base` 取原最小 x，**不整体平移画布**（用户摆过的位置尽量别动）。
 */
export function reorderShotX(
  shots: ShotRef[],
  index: number,
  dir: -1 | 1,
): Map<string, number> | null {
  const j = index + dir;
  if (index < 0 || index >= shots.length || j < 0 || j >= shots.length) return null;

  const swapped = [...shots];
  [swapped[index], swapped[j]] = [swapped[j], swapped[index]];

  const base = Math.min(...shots.map((s) => s.x));
  return new Map(swapped.map((s, k) => [s.id, base + k * SHOT_GAP_X]));
}
