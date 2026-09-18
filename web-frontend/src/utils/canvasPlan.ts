/**
 * 画布「成片」的每段时长解析（2026-09-19 修 #21 时抽出）。
 *
 * ## 为什么单独一个函数
 *
 * `ImageVideoPage` 里的 `plan` 原来是**边遍历边赋值**：
 *
 * ```ts
 * let videoSeconds = 4;
 * for (const id of chain) {
 *   if (node.type === 'videoNode') videoSeconds = node.data.seconds || 4;   // 永远在图片节点之后
 *   else if (node.type === 'imageNode') segments.push({ seconds: videoSeconds, ... });
 * }
 * ```
 *
 * 而 `chain` 的构造决定了成片节点**排在它上游的图片节点之后** ⇒ 每个图片节点 push 时
 * `videoSeconds` 还是初值 4。实测（复刻脚本）：场景「1 图 + 成片 6s」发出的是
 * `seconds: 4`；三张图串联时 `[4,4,4]`。而顶栏用的是 `plan.videoSeconds`（= 6）
 * ⇒ **用户改了时长、界面也显示改了，实际提交的段全是 4 秒**。
 *
 * 所以正确做法是：成片节点的秒数是**全链设置**，先扫一遍再逐段取用。
 */
export function filmSecondsFromChain(
  chain: string[],
  nodes: { id: string; type?: string; data?: { seconds?: number } }[],
): number {
  const byId = new Map(nodes.map((n) => [n.id, n] as const));
  // 取**最后一个**成片节点的设置：多个成片节点时，顶栏显示的也是最后那个，
  // 两者必须一致（否则又变成"界面一套、提交另一套"）。
  let seconds = 4;
  for (const id of chain) {
    const node = byId.get(id);
    if (node?.type === 'videoNode') {
      seconds = node.data?.seconds || 4;
    }
  }
  return seconds;
}
