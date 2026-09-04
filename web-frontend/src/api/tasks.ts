import client, { unwrap } from './client';
import type { CreateTaskRequest, TaskResponse } from '../types/task';

// 任务相关接口。组件不直接发请求，一律走这里。
export async function createVideoTask(req: CreateTaskRequest): Promise<TaskResponse> {
  return unwrap(client.post('/tasks/video', req));
}

export async function getTask(id: number): Promise<TaskResponse | null> {
  return unwrap(client.get(`/tasks/${id}`));
}

// 拉取历史任务列表（后端倒序返回，limit 控制条数）
export async function listTasks(limit = 20): Promise<TaskResponse[]> {
  return unwrap(client.get('/tasks', { params: { limit } }));
}

// 删除历史作品（仅终态任务可删，非终态后端返回 400）
export async function deleteTask(id: number): Promise<void> {
  return unwrap(client.delete(`/tasks/${id}`));
}

// 重新生成历史作品（仅终态任务可发起，后端复用原 prompt/genType 提交新任务）
export async function regenerateTask(id: number): Promise<TaskResponse> {
  return unwrap(client.post(`/tasks/${id}/regenerate`));
}