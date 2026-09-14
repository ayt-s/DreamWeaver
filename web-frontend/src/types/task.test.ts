import { describe, expect, it } from 'vitest';

import { finalVideoUrl, segmentVideoUrls } from './task';

/**
 * P2-6 回归：成片判定不能要求 urls.length > 1。
 *
 * 「合成视频」（image_slideshow）的 result_json 只有一个元素 [final.mp4]，
 * 按 >1 判断会返回 null → 卡片把成片当成"分段"渲染（成片区域空着）。
 */
describe('finalVideoUrl / segmentVideoUrls', () => {
  const FINAL = '/v1/files/abc123/final.mp4';
  const SEG0 = 'https://platform-outputs.agnes-ai.space/videos/task_a.mp4';
  const SEG1 = 'https://platform-outputs.agnes-ai.space/videos/task_b.mp4';

  it('单元素本地产物也要识别成成片（合成视频场景）', () => {
    const json = JSON.stringify([FINAL]);
    expect(finalVideoUrl(json)).toBe(FINAL);
    expect(segmentVideoUrls(json)).toEqual([]);
  });

  it('画布模式 [成片, 分段...] → 成片单独取出，其余是分段', () => {
    const json = JSON.stringify([FINAL, SEG0, SEG1]);
    expect(finalVideoUrl(json)).toBe(FINAL);
    expect(segmentVideoUrls(json)).toEqual([SEG0, SEG1]);
  });

  it('拼接失败（只有 agnes 分段）→ 没有成片，全部按分段展示', () => {
    const json = JSON.stringify([SEG0, SEG1]);
    expect(finalVideoUrl(json)).toBeNull();
    expect(segmentVideoUrls(json)).toEqual([SEG0, SEG1]);
  });

  it('单段 agnes 产物不算成片（标准模式单镜）', () => {
    const json = JSON.stringify([SEG0]);
    expect(finalVideoUrl(json)).toBeNull();
    expect(segmentVideoUrls(json)).toEqual([SEG0]);
  });

  it('空值/非法 JSON 容错', () => {
    expect(finalVideoUrl(null)).toBeNull();
    expect(finalVideoUrl('')).toBeNull();
    expect(finalVideoUrl('not-json')).toBeNull();
    expect(segmentVideoUrls(null)).toEqual([]);
  });
});
