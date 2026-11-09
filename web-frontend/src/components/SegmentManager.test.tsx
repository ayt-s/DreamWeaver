/**
 * 按段重生面板 · 提示词机检警告展示（agent `utils/prompt_lint.py` 的消费端）。
 *
 * 背景：机检只 WARN、不阻断提交，但此前结果只落在 `segments_json` 里，**前端没有任何展示**
 * —— 等于装了报警器不接警铃。这里锁定三条语义：
 * 1. 有警告必须显示条数与每条判据码 + 说明（用户能照着改）；
 * 2. **空数组不显示**（查过没事，不该制造噪音）；
 * 3. **缺 `prompt_lint` 键也不显示**（老任务没查过 —— 不能暗示「已检查且没问题」）。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import SegmentManager from './SegmentManager';
import type { TaskSegment } from '../api/tasks';

vi.mock('../api/tasks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tasks')>();
  return { ...actual, getTaskSegments: vi.fn().mockResolvedValue([]), reworkTask: vi.fn() };
});

function seg(over: Partial<TaskSegment> = {}): TaskSegment {
  return {
    index: 0,
    prompt: '中景，陈浔蹲在灶前拨火',
    seconds: 5,
    ...over,
  };
}

function renderWith(segments: TaskSegment[]) {
  render(
    <SegmentManager
      taskId={1}
      onClose={() => {}}
      onChanged={() => {}}
      segments={segments}
      genType="image_video"
    />,
  );
}

describe('SegmentManager 提示词机检', () => {
  it('有警告时显示条数与每条判据', () => {
    renderWith([
      seg({
        prompt_lint: [
          {
            code: 'EN_NO_SOUND',
            level: 'WARN',
            rule: 'spec-sound',
            msg: '没有声音描述 —— 成片音轨会由模型自由发挥。',
          },
          {
            code: 'CN_GAZE_ON_PROP',
            level: 'WARN',
            rule: 'C18c',
            msg: '有道具动作但没写视线落点。',
          },
        ],
      }),
    ]);
    expect(screen.getByText('2 条提示词警告')).toBeTruthy();
    expect(screen.getByText('EN_NO_SOUND', { exact: false })).toBeTruthy();
    expect(screen.getByText(/没有声音描述/)).toBeTruthy();
    expect(screen.getByText(/视线落点/)).toBeTruthy();
  });

  it('空数组（查过没事）不显示任何警告', () => {
    renderWith([seg({ prompt_lint: [] })]);
    expect(screen.queryByText(/条提示词警告/)).toBeNull();
  });

  it('缺 prompt_lint 键（老任务没查过）也不显示', () => {
    renderWith([seg()]);
    expect(screen.queryByText(/条提示词警告/)).toBeNull();
  });
});
