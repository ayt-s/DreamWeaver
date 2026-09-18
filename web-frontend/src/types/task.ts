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
  | 'expired'
  | 'interrupted';

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
  interrupted: '已中断',
};

/**
 * 格式化任务耗时（completedAt - 起始时刻）。
 * 起始时刻优先传 startedAt（本轮生成起点 = Agent 受理时刻），这样得到的是「实际生成耗时」，
 * 不含排队等待、服务停机、中断重试的空档；startedAt 缺失（历史数据 / 中断后迟到完成）
 * 才回退 createdAt，此时结果含等待，调用方文案要说明。
 * 返回如 "5 分 23 秒"；参数缺失或异常返回 null。
 */
export function formatDuration(completedAt: string | undefined, startedAt: string | undefined): string | null {
  if (!completedAt || !startedAt) return null;
  const end = new Date(completedAt).getTime();
  const start = new Date(startedAt).getTime();
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
  /** 段配置 JSON 字符串（有值时支持按段重生） */
  segmentsJson?: string;
  /** 本轮生成起点（ISO 格式）：Agent 受理时刻；算「实际生成耗时」用，缺失时回退 createdAt */
  startedAt?: string;
  /** 终态完成/失败时间（ISO 格式） */
  completedAt?: string;
  /** 任务创建时间（ISO 格式） */
  createdAt?: string;
  /**
   * 产物来源：'default'（进画廊）/ 'canvas_asset'（画布素材，画廊默认过滤）。
   * 后端刚补上这个字段（此前只有 DB 有这列），面板分源筛选走的是查询参数 `source`。
   */
  source?: string;
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
 * 因此「成片」= 画布模式首项（仅当 >1 个且首项是本地 /v1/files 产物）；
 * 「分段」= 其余项。标准模式无成片，所有项都是独立视频。
 *
 * 判定只认「本地产物路径」：agent 拼接成片固定落在 /v1/files/**；
 * agnes 侧产物走 CDN（platform-outputs.agnes-ai.space），永远不含该前缀。
 * 不再按文件名（如 /final.mp4）猜测——分段 URL 若恰好含该串会被误判为成片并从分段列表中剔除。
 */
export function finalVideoUrl(resultJson?: string | null): string | null {
  const urls = parseResultUrls(resultJson);
  const first = urls[0];
  // 本地拼接产物路径（agent /v1/files/**）才认定为成片；agnes 直链不算。
  //
  // 不再要求 urls.length > 1：「合成视频」（image_slideshow）的 result_json 就只有
  // 一个元素 [final.mp4]，按 >1 判断会让它被当成分段渲染（成片区域空着）。
  // 这条放宽是安全的：result_json 里的分段永远是 agnes CDN URL，
  // 本地 /v1/files/ 项按构造只可能是 synthesizer / slideshow 拼出的成片。
  if (first && first.startsWith('/v1/files/')) {
    return first;
  }
  return null;
}

/** 分段视频 URL（去掉拼接成片），用于段列表 UI 与成片区分 */
export function segmentVideoUrls(resultJson?: string | null): string[] {
  const urls = parseResultUrls(resultJson);
  const final = finalVideoUrl(resultJson);
  return final ? urls.filter((u) => u !== final) : urls;
}

/**
 * 解析 imageUrls JSON 为图片 URL 列表（容错同 parseResultUrls）。
 *
 * 过滤空串：段重生时生成失败的段会以 "" 占位以保持索引对齐（Java 侧按索引取图），
 * 但空串在 UI 上会渲染成破图，且会污染「合成视频」的 ≥2 张判定，故展示层一律剔除。
 */
export function parseImageUrls(imageUrls?: string | null): string[] {
  if (!imageUrls) return [];
  try {
    const parsed = JSON.parse(imageUrls);
    return Array.isArray(parsed)
      ? parsed.filter((u): u is string => typeof u === 'string' && u.trim() !== '')
      : [];
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
  /**
   * 直出图（画布节点「一键文生图」）：true 时 agent 跳过需求解析/剧本/分镜，直接出图。
   * 不传的话单镜 prompt 会被 LLM 重新拆镜 —— 一个节点白生成一堆用不上的图。
   */
  directImage?: boolean;
  /** 直出图候选张数（1~5，默认 1）：同 prompt 多次请求，产出多张供人选一张 */
  imageCount?: number;
  /**
   * 出图画幅（如 '16:9' / '9:16'）。
   *
   * ⚠️ **必须传**：agnes 图片接口不传 ratio 时按 1:1 出图 —— 实测 18/18 张真实产物
   * 都是 1024×1024 正方形，而视频链路是 16:9（首帧是画面的真正基底，正方形基底
   * 会被视频模型先重构图一次）。取所在节点的 ratio。
   */
  imageRatio?: string;
  /**
   * 参考图 JSON 数组字符串（如 `["https://…"]`）。
   *
   * 用途：① 标准模式 = 用户提供的参考图（agent 侧有它就不自动生图，尊重用户输入）；
   * ② 直出图（画布节点「一键文生图」）= **锚定图**，会作为 `extra_body.image`
   * 走图生图（2026-09-18 实测：场景锚图能明显把参考场景带进首帧，
   * 角色锚图收益弱但不会锁脸 —— 见 utils/anchors.ts `firstFrameRefsFor`）。
   */
  referenceImages?: string;
  /**
   * 首帧锁定（keyframe）：把首帧图当作视频的**实际第一帧**。
   *
   * agnes 的 reference 模式官方定义是「内容/风格/运动参考，可能重新构图、重新计时」
   * —— 所以此前那张首帧图并不是视频起点。agent 侧默认开；传 false 回旧行为。
   */
  lockFirstFrame?: boolean;
  /** 段间衔接：下一段的首帧当本段尾帧（last_frame），让相邻段首尾接得上。默认关 */
  chainFrames?: boolean;
  /** 产物来源：默认 default（进画廊）；canvas_asset = 画布素材（画廊默认过滤） */
  source?: string;
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
  /**
   * ⚠️ 服务端发的是 **snake_case**（app/events.py 的 event/emit 原文）：`event_id` / `session_id`。
   * 这里原先声明成 camelCase，导致轨迹面板读 `ev.data.nodeId` 永远取不到值
   * （节点名一直显示不出来）—— 保留 camel 名作为可选别名只为兼容旧引用。
   */
  event_id?: number;
  session_id?: string;
  eventId?: number;
  sessionId?: string;
  type: CreativeEventType;
  timestamp: number;
  data: {
    node_id?: string;
    node_name?: string;
    tool_name?: string;
    summary?: string;
    /** 进度事件的阶段文案（如「下载分段」「拼接完成」） */
    phase?: string;
    progress?: number;
    message?: string;
    error?: string;
    // 历史遗留别名（运行时可能是 undefined）
    nodeId?: string;
    nodeName?: string;
    toolName?: string;
    toolParams?: unknown;
    toolResult?: unknown;
  };
}