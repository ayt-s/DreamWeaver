// 直接调 agent 的 /v1/novel/anchors（前端 vite 已配置 /v1 代理到 8000）
const AGENT_BASE = '/v1';

export interface AnchorRefs {
  characters: Record<string, string>;
  scenes: Record<string, string>;
}

export interface NovelAnchorsRequest {
  characters: Record<string, string>;
  scenes: string[];
  style?: string;
  maxPerType?: number;
}

export type NovelAnchorsResponse = AnchorRefs;

/**
 * 调用 agent 生成角色/场景锚定图。
 * 单个请求约 2-3 分钟（并发 6-10 张图，每张 15-25s）。
 */
export async function generateAnchors(req: NovelAnchorsRequest): Promise<NovelAnchorsResponse> {
  const resp = await fetch(`${AGENT_BASE}/novel/anchors`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`锚定图生成失败 (${resp.status}): ${text.slice(0, 200)}`);
  }
  const json = await resp.json();
  if (json.code !== 0) {
    throw new Error(json.message || '锚定图生成失败');
  }
  return json.data;
}
