import axios from 'axios';

// 与 api/agent.ts 同一实例语义：/v1 → FastAPI 8000
const enrichClient = axios.create({
  baseURL: '/v1',
  timeout: 90_000, // LLM 生成可能 30s+，放宽
});

export interface EnrichResponseData {
  prompt: string;
  gen_type: string;
}

interface EnrichResponse {
  code: number;
  message: string;
  data: EnrichResponseData;
}

/** AI 丰富提示词：把用户简短描述扩展成适合文生图/文生视频的高质量提示词 */
export async function enrichPrompt(
  prompt: string,
  genType: 'text_image' | 'text_video',
): Promise<string> {
  const resp = await enrichClient.post<EnrichResponse>('/agent/enrich-prompt', {
    prompt,
    gen_type: genType,
  });
  if (resp.data.code !== 0) {
    throw new Error(resp.data.message || 'AI 丰富失败');
  }
  return resp.data.data.prompt;
}
