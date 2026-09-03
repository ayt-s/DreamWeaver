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

// 生成类型（与 Java/模型侧 gen_type 对齐）
export type GenType = 'text_video' | 'image_video' | 'novel_image';

export const GEN_TYPE_LABEL: Record<GenType, string> = {
  text_video: '文生视频',
  image_video: '图生视频',
  novel_image: '小说转图',
};

// 任务响应（对应 Java TaskResponse dto）
export interface TaskResponse {
  id: number;
  sessionId: string;
  status: TaskStatus;
  /** 生成类型：text_video/image_video/novel_image */
  genType?: GenType;
  /** 生成产物 JSON（视频 URL 数组字符串），完成后解析展示 */
  resultJson?: string;
  /** 文生图产出的图片 URL 数组（JSON 字符串） */
  imageUrls?: string;
  errorMessage?: string;
}

/** 解析 resultJson 为视频 URL 列表（容错：null/非法 JSON → 空数组） */
export function parseResultUrls(resultJson?: string | null): string[] {
  if (!resultJson) return [];
  try {
    const parsed = JSON.parse(resultJson);
    return Array.isArray(parsed) ? parsed.filter((u): u is string => typeof u === 'string') : [];
  } catch {
    return [];
  }
}

/** 解析 imageUrls JSON 为图片 URL 列表（容错同 parseResultUrls） */
export function parseImageUrls(imageUrls?: string | null): string[] {
  if (!imageUrls) return [];
  try {
    const parsed = JSON.parse(imageUrls);
    return Array.isArray(parsed) ? parsed.filter((u): u is string => typeof u === 'string') : [];
  } catch {
    return [];
  }
}

// 提交任务请求
export interface CreateTaskRequest {
  prompt: string;
  userId?: string;
  /** 生成类型：text_video(默认)/image_video/novel_image */
  genType?: GenType;
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