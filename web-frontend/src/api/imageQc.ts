/**
 * 首帧质检：`POST /v1/qc/images`（agent-service，vite 已把 `/v1` 代理到 8000）。
 *
 * 用途：候选图出来后问一次「哪张踩了『严禁面部特写』红线」（见
 * `agent-service/app/tools/image_qc.py` 的阈值标定），命中就在候选缩略图上打标。
 *
 * 刻意**不落库**：`creative_task` 的 JSON 列都被 Java 当 URL 数组/请求参数解析，
 * 塞不进对象；加列又要动 schema 与 Java 契约。所以这里按需拉、只存组件缓存。
 */
export type ImageQcVerdict = {
  index: number;
  url: string;
  skipped: boolean;
  reason?: string;
  closeup: boolean;
  faces: number;
  faceSpan: number;
};

export type ImageQcSummary = {
  total: number;
  closeupCount: number;
  recommendIndex: number | null;
};

export type ImageQcResponse = { results: ImageQcVerdict[]; summary: ImageQcSummary };

/** 一次最多质检几张 —— 与 agent 侧 `MAX_IMAGES` 对齐，超了接口会 422。 */
export const MAX_QC_IMAGES = 8;

/**
 * 质检一组图片。**失败返回 null**（调用方静默降级：没有徽标，不影响出图流程）——
 * 质检是附加信息，不该让画布报错。
 */
export async function qcImages(urls: string[]): Promise<ImageQcResponse | null> {
  const list = urls.filter((u) => !!u).slice(0, MAX_QC_IMAGES);
  if (list.length === 0) return null;
  try {
    const res = await fetch('/v1/qc/images', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: list }),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { code?: number; data?: ImageQcResponse };
    return body?.data ?? null;
  } catch {
    return null;
  }
}
