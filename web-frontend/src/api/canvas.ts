import client, { unwrap } from './client';

// 无限画布项目接口（按自定义名称持久化画布）
export interface CanvasProjectView {
  id: number;
  name: string;
  updatedAt?: string;
  nodesJson?: string | null;
  edgesJson?: string | null;
  parentId?: number | null;
  /** 角色锚定图 JSON（{"角色名": "url"}） */
  characterRefs?: string | null;
  /** 场景锚定图 JSON（{"场景名": "url"}） */
  sceneRefs?: string | null;
  /** 乐观锁版本号：保存时回传，冲突时后端拒绝写入 */
  version?: number;
}

/**
 * 保存画布的结果。
 * conflict=true 表示乐观锁版本不符（画布已在别处被修改），本次未写入，
 * 并带回服务端现状供前端决定「用我的覆盖」还是「放弃我的改动」。
 */
export interface SaveCanvasResult {
  canvas?: CanvasProjectView | null;
  conflict: boolean;
  serverVersion?: number | null;
  serverNodesJson?: string | null;
  serverEdgesJson?: string | null;
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

/** 保存画布内容 / 重命名 / 锚定图（只更新传入的字段） */
export function saveProject(
  id: number,
  body: {
    name?: string;
    nodesJson?: string;
    edgesJson?: string;
    characterRefs?: string;
    sceneRefs?: string;
    /** 本地基于的版本号；不一致说明画布已被别处修改 → 返回 conflict=true 且不写入 */
    version?: number;
  },
): Promise<SaveCanvasResult> {
  return unwrap(client.put(`/canvas/${id}`, body));
}

/**
 * 轻量版本查询（画布页 5s 轮询用）。
 * 只回版本号/时间，不拉 nodesJson —— 探活不能变成拖库。
 */
export function getCanvasVersion(
  id: number,
): Promise<{ id: number; version?: number; updatedAt?: string }> {
  return unwrap(client.get(`/canvas/${id}/version`));
}

/** 删除项目 */
export function deleteProject(id: number): Promise<void> {
  return unwrap(client.delete(`/canvas/${id}`));
}