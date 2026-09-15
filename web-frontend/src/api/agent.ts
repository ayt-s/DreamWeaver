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

/**
 * 单轮文本生成（画布文本节点的「AI 生成/改写」）。
 * 与 agentChat 的区别：不带画布工具、不走对话循环，直接返回一段纯文本，快且干净。
 */
export async function generateText(instruction: string, context = ''): Promise<string> {
  const resp = await agentClient.post<{ code: number; message: string; data?: { text?: string } }>(
    '/text/generate',
    { instruction, context },
  );
  if (resp.data.code !== 0) {
    throw new Error(resp.data.message || 'AI 生成失败');
  }
  return (resp.data.data?.text ?? '').trim();
}

/** 单镜质检结果（agent nodes/qc.py 产出） */
export interface QcShotReport {
  index: number;
  path?: string;
  total_frames?: number;
  black_frame_ratio?: number;
  blur_frame_ratio?: number;
  duration?: number;
  duration_expected?: number | null;
  aspect_ratio?: string;
  passed: boolean;
  error: string;
}

export interface QcReport {
  passed: boolean;
  shots: QcShotReport[];
  failed_shots: number[];
  total_shots: number;
  reason: string;
}

/**
 * 极简轨迹条目（agent `state.trace`，批次 C1/C3）。
 *
 * **只有三个字段，且不含提示词正文** —— LLM 调用级细节（提示词/模型/重试）走
 * LangSmith，不进 state。别指望从这里拿到 prompt：那是刻意的决定
 * （每个 checkpoint 都会全量进 Redis 快照，正文写两遍没有收益）。
 *
 * `node` 可能是节点级（`video_generator`），也可能带 1-based 序号
 * （`video_generator#2`，表示第 2 镜/第 2 张）。序号是逐镜进度唯一的信息载体。
 * `elapsed_ms === 0` 表示这是一条**进度标记**而非带耗时的条目
 * （视频镜次是批量等待的，等多久无法归因到单镜）。
 */
export interface TraceEntry {
  node: string;
  status: string;
  elapsed_ms: number;
}

export interface AgentTaskState {
  session_id: string;
  status: string;
  video_urls?: string[] | null;
  error_message?: string | null;
  /** QC 未跑的链路（图片任务 / 合成视频）为 null —— 与「质检通过」区分开 */
  qc_report?: QcReport | null;
  /** 链路轨迹快照；缺失时为 `[]`（后端已保证不是 null） */
  trace?: TraceEntry[] | null;
}

/**
 * 读取 agent 侧会话状态（GET /v1/tasks/{sessionId}）。
 *
 * 为什么需要单独调 agent：任务详情走 Java（/api/tasks/{id}），而 qc_report 只在
 * agent 的 state 里。Java 侧的 error_message 只有一句汇总（notify_final 写入），
 * 逐镜原因必须从这里取。
 *
 * 失败返回 null（而不是抛）：agent 可能没起、或会话快照已过期，
 * 轨迹面板不该因此报错。
 */
export async function agentTaskState(sessionId: string): Promise<AgentTaskState | null> {
  try {
    const resp = await agentClient.get<ChatResponse>(`/tasks/${sessionId}`);
    if (resp.data.code !== 0) return null;
    return (resp.data.data as unknown as AgentTaskState) ?? null;
  } catch {
    return null;
  }
}
