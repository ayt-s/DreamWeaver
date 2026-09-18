import { describe, expect, it } from 'vitest';

import { segmentPromptText } from './segmentText';

/**
 * 段描述取值（2026-09-19 修 #29 的回归测试）。
 *
 * 真实数据（只读接口 `GET /api/tasks/54/segments`）：标准模式的段只有 `cn_description`、
 * **没有 `prompt`** ⇒ 面板此前显示「（空提示词）」、编辑框空白。
 */
describe('segmentPromptText', () => {
  it('标准模式的段：只有 cn_description 时也能取到描述（这就是原来的 bug）', () => {
    const seg = {
      cn_description: '一位穿白色连衣裙的女子背对镜头，赤脚走在清晨湿润的沙滩上。',
      prompt_en: 'A woman in a white dress...',
      shot_id: 's1',
    };
    expect(segmentPromptText(seg)).toContain('白色连衣裙');
  });

  it('画布/图片任务：优先用用户自己写的 prompt', () => {
    expect(segmentPromptText({ prompt: '用户写的', cn_description: '模型写的' })).toBe('用户写的');
  });

  it('prompt 是空白串时回落 cn_description（空格不算内容）', () => {
    expect(segmentPromptText({ prompt: '   ', cn_description: '模型写的' })).toBe('模型写的');
  });

  it('两者都没有 → 空串（调用方据此显示"空提示词"占位）', () => {
    expect(segmentPromptText({})).toBe('');
    expect(segmentPromptText(null)).toBe('');
    expect(segmentPromptText(undefined)).toBe('');
  });
});
