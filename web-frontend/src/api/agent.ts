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
  /** 工具返回值的**摘要**（后端截断到 400 字符，超长时 truncated=true）。
   *  2026-09-23 起后端会跨消息配对真实返回值；此前恒为空对象。 */
  result?: string;
  /** result 是否被截断（如 inspect_canvas 的整份画布 JSON） */
  truncated?: boolean;
  /** ok = 工具成功返回 / error = 抛错或被模型重试提示 / called = 调了但轨迹里没看到返回值 */
  status: string;
}

export interface ChatUsage {
  requests: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface ChatResponseData {
  reply: string;
  tool_calls: ChatToolCall[];
  /** 本轮真实消耗（请求数 / 工具调用数 / tokens），后端 2026-09-23 起返回 */
  usage?: ChatUsage;
  model?: string;
  /** 后端实际使用的历史来源：server = 服务端结构化真历史，client = 前端拍平的文本历史 */
  history_source?: 'server' | 'client';
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

/**
 * 调用 agent 聊天接口（POST /v1/agent/chat）。
 *
 * `conversationId` 由前端按画布生成并持久化：给了它服务端就保管**结构化历史**
 * （含工具调用与返回值），本轮只发 message；服务端没有历史时（Redis 降级 / 历史过期）
 * 会自动回落到 `history` 这段拍平文本。两条都发是有意的 —— 回落路径需要它。
 */
export async function agentChat(
  canvasId: number | null,
  message: string,
  history: ChatHistoryItem[],
  conversationId?: string,
): Promise<ChatResponseData> {
  const resp = await agentClient.post<ChatResponse>('/agent/chat', {
    canvas_id: canvasId,
    message,
    history,
    conversation_id: conversationId ?? null,
  });
  if (resp.data.code !== 0) {
    throw new Error(resp.data.message || 'agent 调用失败');
  }
  return resp.data.data;
}

/**
 * 清掉某段对话的服务端历史（「清空对话」时调用）。
 *
 * ⚠️ 不调的话是典型的「界面说清空了、其实没清」：气泡删了，服务端历史还在，
 * 下一轮 agent 仍会引用已经不在屏幕上的对话。
 */
export async function clearChatHistory(conversationId: string): Promise<void> {
  await agentClient.delete(`/agent/chat/${encodeURIComponent(conversationId)}`);
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
  /** 低细节帧占比（Laplacian 方差口径）。**参考指标**：自 2026-09-18 起不再参与判定，
   *  因为实测被判「低细节过半」的段人眼都清晰（夜景/柔光/暗场特效）。 */
  blur_frame_ratio?: number;
  /** 空帧（纯色/全黑无内容）占比，零容忍 */
  flat_frame_ratio?: number;
  /** 确定性失败成因（"truncated" / "black_frames" / "flat_frames"）——比 error 文案更适合分流 */
  failed_reasons?: string[];
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
