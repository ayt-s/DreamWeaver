import client, { unwrap } from './client';
import type {
  NovelPreprocessRequest,
  NovelProject,
  NovelSegment,
  ToCanvasResult,
} from '../types/novel';

/**
 * 提交预处理（同步等待 agent）。
 * 前端超时必须大于后端对 agent 的超时（180s），否则后端还在等、前端先放弃。
 */
export function preprocessNovel(req: NovelPreprocessRequest): Promise<NovelProject> {
  return unwrap(client.post('/novel/preprocess', req, { timeout: 240_000 }));
}

/** 删除小说项目记录（只删本记录；它生成的画布项目不受影响） */
export function deleteNovelProject(id: number): Promise<void> {
  return unwrap(client.delete(`/novel/${id}`));
}

/** 查询项目 */
export function getNovelProject(id: number): Promise<NovelProject> {
  return unwrap(client.get(`/novel/${id}`));
}

/** 项目列表（轻量字段：不含 novelText/segments，按更新时间倒序，上限 50） */
export function listNovelProjects(): Promise<NovelProject[]> {
  return unwrap(client.get('/novel'));
}

/** 更新分镜列表 */
export function updateSegments(
  id: number,
  segments: NovelSegment[],
): Promise<NovelProject> {
  return unwrap(client.put(`/novel/${id}/segments`, { segments }));
}

/**
 * 转画布：把分镜落成 canvas project（后端幂等：已绑定画布则复用更新）。
 * 锚定图随请求落库到 canvas_project.character_refs/scene_refs，
 * 这样刷新画布、换设备都还在（此前只靠 URL query + localStorage）。
 *
 * 覆盖保护：目标画布已被改过时后端**不写库**，返回 needConfirm；
 * 用户选「覆盖」传 force=true，选「另存为新画布」传 saveAsNew=true。
 */
export function toCanvas(
  id: number,
  anchors?: { characters?: Record<string, string>; scenes?: Record<string, string> },
  opts?: { force?: boolean; saveAsNew?: boolean },
): Promise<ToCanvasResult> {
  const body = {
    ...(anchors
      ? {
          characterRefs: JSON.stringify(anchors.characters ?? {}),
          sceneRefs: JSON.stringify(anchors.scenes ?? {}),
        }
      : {}),
    ...(opts?.force ? { force: true } : {}),
    ...(opts?.saveAsNew ? { saveAsNew: true } : {}),
  };
  return unwrap(client.post(`/novel/${id}/to-canvas`, body));
}
