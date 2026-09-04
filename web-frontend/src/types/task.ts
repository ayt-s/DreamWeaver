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
export type GenType = 'text_video' | 'image_video' | 'text_image';

export const GEN_TYPE_LABEL: Record<GenType, string> = {
  text_video: '文生视频',
  image_video: '图生视频',
  text_image: '文生图',
};

/** 任务状态中文文案（展示用，避免把英文状态码直接抛给用户） */
export const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  queued: '排队中',
  script_writing: '剧本编写中',
  storyboard_writing: '分镜拆解中',
  asset_generating: '素材生成中',
  video_generating: '视频生成中',
  qc_checking: '质量检查中',
  fix_looping: '修复重试中',
  synthesizing: '合成中',
  completed: '已完成',
  failed: '已失败',
  expired: '已过期',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.replace(/_/g, ' ');
}

/** 会话 ID 截断展示：保留前 6 位，完整值放 title 悬浮提示 */
export function shortSessionId(full: string | undefined): string {
  if (!full) return '';
  return full.length > 6 ? `${full.slice(0, 6)}…` : full;
}

// 任务响应（对应 Java TaskResponse dto）
export interface TaskResponse {
  id: number;
  sessionId: string;
  status: TaskStatus;
  /** 生成类型：text_video/image_video/text_image */
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
  /**
   * 生成类型：text_video(默认)/image_video/text_image。
   * image_video 可搭配 segments 走无限画布模式；text_image 只出图。
   */
  genType?: GenType;
  /**
   * 无限画布图生视频片段（JSON 字符串：[{image_url, prompt, seconds}]）。
   * 每段一张参考图 + 一段视频内容描述，生成几秒小视频后由模型侧拼接成长视频。
   */
  segments?: string;
}

/** 无限画布片段（前端编辑态，提交时序列化为 CreateTaskRequest.segments） */
export interface CanvasSegment {
  imageUrl: string;
  prompt: string;
  seconds: number;
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