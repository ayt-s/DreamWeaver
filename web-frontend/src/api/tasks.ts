import client, { unwrap } from './client';
import type {
  CreateTaskRequest,
  DraftFilter,
  GenType,
  TaskListResponse,
  TaskResponse,
} from '../types/task';

// 任务相关接口。组件不直接发请求，一律走这里。
export async function createVideoTask(req: CreateTaskRequest): Promise<TaskResponse> {
  return unwrap(client.post('/tasks/video', req));
}

export async function getTask(id: number): Promise<TaskResponse | null> {
  return unwrap(client.get(`/tasks/${id}`));
}

// 拉取历史任务分页列表（倒序；支持 genType 分类 + draft 草稿二维筛选）
export async function listTasks(params: {
  page?: number;
  size?: number;
  genType?: GenType | '';
  draft?: DraftFilter;
}): Promise<TaskListResponse> {
  const { page = 1, size = 10, genType = '', draft = '' } = params;
  return unwrap(
    client.get('/tasks', {
      params: {
        page,
        size,
        genType: genType || undefined,
        // '' = 不筛；'final' -> false；'draft' -> true
        draft: draft === '' ? undefined : draft === 'draft',
      },
    }),
  );
}

// 切换草稿标记：draft=true 移入草稿区，false 移出（回成品区）；仅终态任务可切换
export async function setTaskDraft(id: number, draft: boolean): Promise<TaskResponse> {
  return unwrap(client.patch(`/tasks/${id}/draft`, null, { params: { draft } }));
}

// 删除历史作品（仅终态任务可删，非终态后端返回 400）
export async function deleteTask(id: number): Promise<void> {
  return unwrap(client.delete(`/tasks/${id}`));
}

// 重新生成历史作品（仅终态任务可发起；同一任务原地重跑，不产生新 id）
export async function regenerateTask(id: number): Promise<TaskResponse> {
  return unwrap(client.post(`/tasks/${id}/regenerate`));
}

// 查询任务的段配置 + 每段已有视频 URL（供按段重生 UI 展示）
export interface TaskSegment {
  index: number;
  prompt: string;
  image_url?: string;
  reference_images?: string[];
  seconds?: number;
  aspect_ratio?: string;
  /** 该段当前视频 URL（重生成后会被替换） */
  existing_video_url?: string;
  /** 该段当前图片 URL（图片任务段重生用） */
  existing_image_url?: string;
  /** 段首张参考图，用作缩略图 */
  thumbnail?: string;
}

export async function getTaskSegments(id: number): Promise<TaskSegment[]> {
  return unwrap(client.get(`/tasks/${id}/segments`));
}

// 按段重生：勾选段重生（可改提示词）+ 未勾选段复用已有视频 + 重新拼接成片
export async function reworkTask(
  id: number,
  req: { reworkIndices: number[]; editedPrompts?: Record<string, string> },
): Promise<TaskResponse> {
  return unwrap(client.post(`/tasks/${id}/rework`, req));
}

// 批量按段重生：一次提交多个任务的段重生
export interface BatchReworkItemReq {
  taskId: number;
  reworkIndices: number[];
  editedPrompts?: Record<string, string>;
}
export interface BatchReworkItemRes {
  taskId: number;
  success: boolean;
  message: string;
  task?: TaskResponse;
}
export interface BatchReworkResult {
  total: number;
  success: number;
  failed: number;
  results: BatchReworkItemRes[];
}
export async function batchReworkTasks(
  items: BatchReworkItemReq[],
): Promise<BatchReworkResult> {
  return unwrap(client.post('/tasks/batch-rework', { items }));
}

// 图片合成视频：挑选已有图片 → ffmpeg 幻灯片拼成片（不消耗 agnes 额度）
export interface SlideshowRequest {
  /** 图片 URL 数组（至少 2 张，必须公网可访问） */
  slideshowImages: string[];
  /** 单张停留秒数，1~10 */
  slideSeconds?: number;
  /** 展示用标题 */
  prompt?: string;
}
export async function createSlideshowTask(req: SlideshowRequest): Promise<TaskResponse> {
  return unwrap<TaskResponse>(
    client.post('/tasks/video', {
      prompt: req.prompt || `图片合成视频（${req.slideshowImages.length} 张）`,
      genType: 'image_video',
      slideshowImages: JSON.stringify(req.slideshowImages),
      slideSeconds: req.slideSeconds ?? 3,
    }),
  );
}

// 本地上传参考图（无限画布用；产物经 /api/uploads/** 静态提供）
// ⚠️ agnes 生成要求公网 URL，本地上传图仅用于画布预览
export async function uploadImage(file: File): Promise<{ url: string; name: string }> {
  const form = new FormData();
  form.append('file', file);
  return unwrap(client.post('/uploads', form));
}