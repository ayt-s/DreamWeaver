import client, { unwrap } from './client';

// 无限画布项目接口（按自定义名称持久化画布）
export interface CanvasProjectView {
  id: number;
  name: string;
  updatedAt?: string;
  nodesJson?: string | null;
  edgesJson?: string | null;
}

/** 创建项目（空画布） */
export function createProject(name: string): Promise<CanvasProjectView> {
  return unwrap(client.post('/canvas', { name }));
}

/** 项目列表（轻量，无 JSON） */
export function listProjects(): Promise<CanvasProjectView[]> {
  return unwrap(client.get('/canvas'));
}

/** 加载项目完整内容 */
export function getProject(id: number): Promise<CanvasProjectView> {
  return unwrap(client.get(`/canvas/${id}`));
}

/** 保存画布内容 / 重命名（只更新传入的字段） */
export function saveProject(
  id: number,
  body: { name?: string; nodesJson?: string; edgesJson?: string },
): Promise<CanvasProjectView> {
  return unwrap(client.put(`/canvas/${id}`, body));
}

/** 删除项目 */
export function deleteProject(id: number): Promise<void> {
  return unwrap(client.delete(`/canvas/${id}`));
}