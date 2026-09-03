// 统一返回体（与后端 CommonResult / ApiResponse 对齐）
export interface CommonResult<T> {
  code: number;
  message: string;
  data: T | null;
}

// 创作任务状态（与后端/模型侧 TaskStatus 对齐）
export type TaskStatus =
  | 'pending'
  | 'queued'
  | 'script_writing'
  | 'storyboard_writing'
  | 'asset_generating'
  | 'video_generating'
  | 'qc_checking'
  | 'fix_looping'
  | 'synthesizing'
  | 'completed'
  | 'failed'
  | 'expired';

// 任务响应（对应 Java TaskResponse dto）
export interface TaskResponse {
  id: number;
  sessionId: string;
  status: TaskStatus;
  errorMessage?: string;
}

// 提交任务请求
export interface CreateTaskRequest {
  prompt: string;
  userId?: string;
}

// SSE 轨迹事件（对应设计文档 §5.1）
export type CreativeEventType =
  | 'session_started'
  | 'node_entered'
  | 'node_completed'
  | 'tool_called'
  | 'tool_result'
  | 'interrupted'
  | 'progress'
  | 'completed'
  | 'failed';

export interface CreativeEvent {
  eventId: number;
  sessionId: string;
  type: CreativeEventType;
  timestamp: number;
  data: {
    nodeId?: string;
    nodeName?: string;
    toolName?: string;
    toolParams?: unknown;
    toolResult?: unknown;
    progress?: number;
    message?: string;
    error?: string;
  };
}