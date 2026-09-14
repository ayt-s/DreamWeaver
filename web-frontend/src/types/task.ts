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
export type GenType = 'text_video' | 'image_video' | 'text_image' | 'comic_video';

export const GEN_TYPE_LABEL: Record<GenType, string> = {
  text_video: '文生视频',
  image_video: '图生视频',
  text_image: '文生图',
  comic_video: '漫剧',
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

/**
 * 格式化任务耗时（completedAt - createdAt）。
 * completedAt 为 ISO 字符串（如 "2026-09-06T12:04:14"），created 为毫秒时间戳。
 * 返回如 "5 分 23 秒"；参数缺失或异常返回 null。
 */
export function formatDuration(completedAt: string | undefined, createdAt: string | undefined): string | null {
  if (!completedAt || !createdAt) return null;
  const end = new Date(completedAt).getTime();
  const start = new Date(createdAt).getTime();
  if (!end || !start || isNaN(end) || isNaN(start)) return null;
  const ms = end - start;
  if (ms < 0) return null;
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec} 秒`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return `${min} 分 ${sec} 秒`;
  const hr = Math.floor(min / 60);
  const m = min % 60;
  return `${hr} 时 ${m} 分`;
}

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
  /** 创作需求原文（画廊卡片标题；重新生成时复用） */
  prompt?: string;
  /** 草稿标记：true=草稿（默认，新生成物进草稿区）false=成品 */
  isDraft?: boolean;
  /** 段配置 JSON 字符串（有值时支持穿帮段重生） */
  segmentsJson?: string;
  /** 终态完成/失败时间（ISO 格式） */
  completedAt?: string;
  /** 任务创建时间（ISO 格式） */
  createdAt?: string;
}

/** 任务分页列表（对应 Java TaskListResponse dto） */
export interface TaskListResponse {
  list: TaskResponse[];
  total: number;
  page: number;
  size: number;
}

/**
 * 画廊分类筛选（待补充生成类型在此数组追加即可；'' = 全部）。
 * key 对应后端 gen_type。
 */
/**
 * 草稿/成品筛选（二维，与 genType 分类正交）。
 * '' = 全部（默认）；'final' = 只看成品；'draft' = 只看草稿。
 * 后端 draft 参数：undefined=不筛，false=成品，true=草稿。
 */
export type DraftFilter = '' | 'final' | 'draft';

export const DRAFT_FILTERS: Array<{ key: DraftFilter; label: string }> = [
  { key: '', label: '全部' },
  { key: 'final', label: '成品' },
  { key: 'draft', label: '草稿' },
];

export const GEN_TYPE_FILTERS: Array<{ key: GenType | ''; label: string }> = [
  { key: '', label: '全部' },
  { key: 'text_image', label: '文生图' },
  { key: 'comic_video', label: '漫剧' },
  { key: 'text_video', label: '文生视频' },
  { key: 'image_video', label: '图生视频' },
];

/**
 * 展示用图片 URL：非本地的 http(s) 图走 Redis 本地缓存出口（/api/images/cache），
 * 避免每次直连 agnes 产物 CDN 加载慢；agnes 侧始终保留原始 URL（生成必须公网可访问）。
 */
export function cachedImageUrl(url: string): string {
  if (
    url.startsWith('http://localhost') ||
    url.startsWith('http://127.0.0.1') ||
    url.startsWith('/') ||
    url.startsWith('data:')
  ) {
    return url;
  }
  return `/api/images/cache?url=${encodeURIComponent(url)}`;
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

/**
 * resultJson 格式说明：画布模式为 [拼接成片, 分段0, 分段1, ...]（成片在最前）；
 * 标准模式为 [视频0, 视频1, ...]（无拼接成片）。
 * 因此「成片」= 画布模式首项（仅当 >1 个且是本地 /v1/files 产物）；
 * 「分段」= 其余项。标准模式无成片，所有项都是独立视频。
 */
export function finalVideoUrl(resultJson?: string | null): string | null {
  const urls = parseResultUrls(resultJson);
  if (urls.length > 1) {
    const first = urls[0];
    // 本地拼接产物路径（agent /v1/files/**）才认定为成片；agnes 直链不算
    if (first.startsWith('/v1/files/') || first.includes('/final.mp4')) {
      return first;
    }
  }
  return null;
}

/** 分段视频 URL（去掉拼接成片），用于段列表 UI 与成片区分 */
export function segmentVideoUrls(resultJson?: string | null): string[] {
  const urls = parseResultUrls(resultJson);
  const final = finalVideoUrl(resultJson);
  return final ? urls.filter((u) => u !== final) : urls;
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
  /** 可选视频模型：agnes-video-2.5-flash（默认） / agnes-video-2.5（HD） */
  videoModel?: string;
  /** 全局风格提示词（折进每镜提示词正文） */
  stylePrompt?: string;
  /** 负面提示词（折成「避免出现：…」进正文） */
  negativePrompt?: string;
  /** 时间轴：期望总时长（秒） */
  totalSeconds?: number;
  /** 时间轴：期望镜头数 */
  shotCount?: number;
  /**
   * 全局运镜倾向 JSON 字符串：{shot_size, angle, movement}（标准模式用）。
   * agent 翻译成确定性英文运镜片段注入每镜提示词。
   */
  shotLanguage?: string;
  /** 元素语义绑定 JSON 字符串：[{name, imageIndex}]，imageIndex 1-based */
  referenceBindings?: string;
}

// === 可灵式结构化运镜（与 agent prompting.py 白名单严格对齐） ===

export interface CameraSpec {
  /** 景别 */
  shot_size?: string;
  /** 机位 */
  angle?: string;
  /** 运镜 */
  movement?: string;
}

export const SHOT_SIZE_OPTIONS = ['远景', '全景', '中景', '近景', '特写'] as const;
export const CAMERA_ANGLE_OPTIONS = ['平视', '俯拍', '仰拍', '航拍', '过肩'] as const;
export const CAMERA_MOVE_OPTIONS = ['固定', '推近', '拉远', '摇镜', '移镜', '跟拍', '环绕'] as const;

export const SHOT_SIZE_LABEL = '景别';
export const CAMERA_ANGLE_LABEL = '机位';
export const CAMERA_MOVE_LABEL = '运镜';

/** 运镜 spec 是否为空（空则不注入提示词） */
export function hasCameraSpec(spec?: CameraSpec | null): boolean {
  if (!spec) return false;
  return Boolean(spec.shot_size || spec.angle || spec.movement);
}

/** 元素语义绑定：把剧本中的名词绑到参考图编号（<Picture N>，1-based） */
export interface ReferenceBinding {
  name: string;
  imageIndex: number;
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