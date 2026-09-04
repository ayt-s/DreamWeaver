import client, { unwrap } from './client';
import type {
  CreateTaskRequest,
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

// 拉取历史任务分页列表（倒序；支持 genType 分类筛选，'' = 全部）
export async function listTasks(params: {
  page?: number;
  size?: number;
  genType?: GenType | '';
}): Promise<TaskListResponse> {
  const { page = 1, size = 10, genType = '' } = params;
  return unwrap(
    client.get('/tasks', {
      params: { page, size, genType: genType || undefined },
    }),
  );
}

// 删除历史作品（仅终态任务可删，非终态后端返回 400）
export async function deleteTask(id: number): Promise<void> {
  return unwrap(client.delete(`/tasks/${id}`));
}

// 重新生成历史作品（仅终态任务可发起；同一任务原地重跑，不产生新 id）
export async function regenerateTask(id: number): Promise<TaskResponse> {
  return unwrap(client.post(`/tasks/${id}/regenerate`));
}

// 本地上传参考图（无限画布用；产物经 /api/uploads/** 静态提供）
// ⚠️ agnes 生成要求公网 URL，本地上传图仅用于画布预览
export async function uploadImage(file: File): Promise<{ url: string; name: string }> {
  const form = new FormData();
  form.append('file', file);
  return unwrap(client.post('/uploads', form));
}