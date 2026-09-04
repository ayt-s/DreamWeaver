import client, { unwrap } from './client';
import type {
  CanvasProjectRef,
  NovelPreprocessRequest,
  NovelProject,
  NovelSegment,
} from '../types/novel';

/** 提交预处理（同步等待 agent，10-60s） */
export function preprocessNovel(req: NovelPreprocessRequest): Promise<NovelProject> {
  return unwrap(client.post('/novel/preprocess', req, { timeout: 120_000 }));
}

/** 查询项目 */
export function getNovelProject(id: number): Promise<NovelProject> {
  return unwrap(client.get(`/novel/${id}`));
}

/** 更新分镜列表 */
export function updateSegments(
  id: number,
  segments: NovelSegment[],
): Promise<NovelProject> {
  return unwrap(client.put(`/novel/${id}/segments`, { segments }));
}

/** 转画布：把分镜落成 canvas project */
export function toCanvas(id: number): Promise<CanvasProjectRef> {
  return unwrap(client.post(`/novel/${id}/to-canvas`));
}
