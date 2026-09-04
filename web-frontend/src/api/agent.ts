import axios from 'axios';

// 独立 axios 实例：dev 走 vite 代理到 agent-service 8000（baseURL 是 /v1，不是 /api）
// 与 api/client.ts 分开：Java API 走 /api，Agent API 走 /v1
const agentClient = axios.create({
  baseURL: '/v1',
  timeout: 120_000, // agent 单次调用可能 60s+，放宽
});

export interface ChatToolCall {
  tool_name: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
  status: string; // called / ok / error（后端 Pydantic AI 轨迹）
}

export interface ChatResponseData {
  reply: string;
  tool_calls: ChatToolCall[];
}

export interface ChatResponse {
  code: number;
  message: string;
  data: ChatResponseData;
}

export interface ChatHistoryItem {
  role: 'user' | 'assistant';
  content: string;
}

/** 调用 agent 聊天接口（POST /v1/agent/chat） */
export async function agentChat(
  canvasId: number | null,
  message: string,
  history: ChatHistoryItem[],
): Promise<ChatResponseData> {
  const resp = await agentClient.post<ChatResponse>('/agent/chat', {
    canvas_id: canvasId,
    message,
    history,
  });
  if (resp.data.code !== 0) {
    throw new Error(resp.data.message || 'agent 调用失败');
  }
  return resp.data.data;
}
