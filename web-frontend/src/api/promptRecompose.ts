/**
 * 存量画布提示词重算：`POST /v1/novel/recompose-prompts`（agent-service，vite 已把 `/v1` 代理到 8000）。
 *
 * ## 为什么需要这个入口
 *
 * 画布里的提示词是**预处理时**合成的：改了 `app/novel/composer.py`（角色锚裁剪 /
 * 动物移出角色锚 / 镜头段清理…）**不会**自动更新存量画布 —— 此前只能手跑技能目录里的脚本
 * `recompose_canvas_prompts.py`。这个端点把那套规则正式化为产品路径。
 *
 * ## 契约与职责边界
 *
 * - agent 端是**纯函数**：只按当前规则算「重算后应当是什么」并说明每条改了什么，
 *   **不碰 DB、不写画布**；
 * - 落库由画布页走**既有乐观锁 PUT**（`/api/canvas/{id}` + `version`）+ **回读确认**，
 *   因为这个页面本来就持有 `version`，在那里落库才能沿用同一套并发保护。
 *
 * 规则实现只有一处（agent 侧 import 真实 composer 函数）—— 前端**不复制**这份逻辑，
 * 只负责展示与确认。
 */
export type RecomposeNodeResult = {
  id: string;
  /** 重算后的提示词（结构不合规被跳过时**逐字等于入参**） */
  prompt: string;
  changed: boolean;
  /** true = 结构不是 [角色锚]；…；[场景] 三段式，没动它 */
  skipped: boolean;
  /** 每条改动原因（中文，直接展示给用户） */
  reasons: string[];
};

export type RecomposeResult = {
  nodes: RecomposeNodeResult[];
  changed_count: number;
  /** 动物判定来源（analyzer 结构化字段 / 关键词表）——「为什么这么改」的依据，如实展示 */
  animal_source: string;
};

export type RecomposeRequestNode = { id: string; prompt: string };

/**
 * 按当前规则重算（dry-run）。**不改任何数据** —— 调用方拿到结果做确认。
 *
 * 失败如实抛错（这是用户主动点的按钮，失败必须可见，不能静默降级）。
 */
export async function recomposePrompts(
  nodes: RecomposeRequestNode[],
  analysis: unknown = null,
): Promise<RecomposeResult> {
  const resp = await fetch('/v1/novel/recompose-prompts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis, nodes }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`提示词重算失败 (${resp.status}): ${text.slice(0, 200)}`);
  }
  const json = (await resp.json()) as { code?: number; message?: string; data?: RecomposeResult };
  if (json.code !== 0) throw new Error(json.message || '提示词重算失败');
  return json.data as RecomposeResult;
}
