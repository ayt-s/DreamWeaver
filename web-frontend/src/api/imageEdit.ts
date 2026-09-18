/**
 * 图像定点修正：`POST /v1/images/edit`（agent-service，vite 已把 `/v1` 代理到 8000）。
 *
 * 用途：对**已有一张图**做定向修改（图生图），而不是重新生成一张。实测（2026-09-18）：
 *
 * - 「删/换局部」可靠：底图「两头黑牛」+ 指令「只保留一头，其余不变」
 *   → 结果只剩一头，人物/服装/姿势/场景/光线/构图全部保持
 * - 「改景别」只达标一半：指令「拉远为中远景」→ 景别真的变远，**但人物外观也被重画**
 * - i2i **对「锁脸」没有优势**（两轮实测），别把它当角色一致性手段
 *
 * 所以这个能力的定位是「局部修正」，UI 文案必须写清，否则用户会拿它去治「大脸」
 * 然后得到一张换了人的图。
 *
 * 刻意**不落库**（与 `imageQc.ts` 同一先例）：结果只是画布节点上的候选，
 * 不该创建 `creative_task`（否则一次修正就在画廊/草稿区刷出一个任务）。
 * 也因此**失败必须抛**（不像质检那样静默降级）——用户主动点的操作，
 * 静默失败等于「点了没反应」。
 */
export type ImageEditOptions = {
  /** 输出画幅（一般传该节点的 ratio，与原图一致） */
  ratio?: string;
  /** 出几张修正结果（1~2） */
  count?: number;
};

export type ImageEditResult = { urls: string[] };

/** 对 `imageUrl` 按 `instruction` 做定点修正，返回新图 URL 列表。 */
export async function editImage(
  imageUrl: string,
  instruction: string,
  options: ImageEditOptions = {},
): Promise<string[]> {
  const res = await fetch('/v1/images/edit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      image_url: imageUrl,
      instruction,
      ratio: options.ratio,
      count: options.count ?? 1,
    }),
  });

  let body: { code?: number; message?: string; data?: ImageEditResult } | null = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok || (body && body.code !== 0)) {
    // 后端的 AppError 文案是中文且可操作（如「修正失败：上游 429」），直接透给用户
    throw new Error(body?.message || `修正请求失败（HTTP ${res.status}）`);
  }
  return body?.data?.urls ?? [];
}
