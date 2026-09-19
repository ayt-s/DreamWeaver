import { describe, expect, it } from 'vitest';

import {
  GEN_TYPE_FILTERS,
  GEN_TYPE_LABEL,
  GEN_TYPE_PRODUCERS,
  finalVideoUrl,
  segmentVideoUrls,
  type GenType,
} from './task';

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

/**
 * ★ 2026-09-19 修（#34）的护栏：画廊筛选按钮必须对应一个**前端真能产出**的类型。
 *
 * 原缺陷：`GEN_TYPE_FILTERS` 里有 `comic_video`（中文标签「漫剧」），但全前端零生产者
 * ——CreatePanel 的生成类型只有 3 项、画布提交只给 image_video/text_video、
 * 合成视频固定 image_video ⇒ 用户点「漫剧」永远是空列表，会以为作品丢了 / 筛选坏了。
 *
 * ⚠️ 这个用例能钉住的是**两份声明的一致性**（筛选项 ↔ 生产者名单），
 *    它没法机械证明「生产者真的存在」——`GEN_TYPE_PRODUCERS` 的注释里逐条写了
 *    生产者所在文件:行，加类型的人要自己去核对那三处。
 */
describe('GEN_TYPE_FILTERS 准入：筛选项 ↔ 生产者（#34）', () => {
  it('筛选项（除「全部」）与前端生产者名单**互为子集**（不是单向）', () => {
    const filterKeys = GEN_TYPE_FILTERS
      .map((f) => f.key)
      .filter((k): k is GenType => k !== '');
    // 有筛选项却没生产者 → 用户点进去永远空列表（#34 的原形状）
    expect(filterKeys.filter((k) => !GEN_TYPE_PRODUCERS.includes(k))).toEqual([]);
    // 有生产者却没筛选项 → 产物在画廊里筛不出来（同一口径的另一半）
    expect([...GEN_TYPE_PRODUCERS].filter((k) => !filterKeys.includes(k))).toEqual([]);
  });

  it('生产者名单的每个类型都能取到中文标签（卡片徽标不回落成英文键）', () => {
    for (const k of GEN_TYPE_PRODUCERS) {
      expect(GEN_TYPE_LABEL[k], k).toBeTruthy();
    }
  });
});
