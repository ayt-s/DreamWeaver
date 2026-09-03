import client, { unwrap } from './client';
import type { CreateTaskRequest, TaskResponse } from '../types/task';

// 任务相关接口。组件不直接发请求，一律走这里。
export async function createVideoTask(req: CreateTaskRequest): Promise<TaskResponse> {
  return unwrap(client.post('/tasks/video', req));
}

export async function getTask(id: number): Promise<TaskResponse | null> {
  return unwrap(client.get(`/tasks/${id}`));
}