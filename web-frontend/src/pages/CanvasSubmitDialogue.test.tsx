/**
 * 台词（dialogue）随段提交 —— 全链路载荷护栏（★ 2026-09-24）。
 *
 * 背景（实测）：落地前全链路**没有台词词位** —— agent 的分镜 schema 与剧本模板都没有
 * `dialogue` 字段，小说里的对话被压进 plot 叙述，模型只能自己编口型对白。
 * Agnes Video 2.5 文档速查表第 2 条要求「台词 = 原文」，且 §2.3 说
 * 「很多口型问题都来自台词与镜头时长不对齐」。
 *
 * 本用例只看**提交载荷**这一段链路：画布节点 data.dialogue → segments[].dialogue。
 * （agent 侧「逐字进提示词」的断言在 agent-service/tests/test_video_prompt_spec.py）
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import ImageVideoPage from './ImageVideoPage';
import { createVideoTask, getTask, uploadImage } from '../api/tasks';
import { getProject, listProjects } from '../api/canvas';

vi.mock('../api/tasks', async (io) => ({
  ...(await io<typeof import('../api/tasks')>()),
  createVideoTask: vi.fn(), getTask: vi.fn(),
  listTasks: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, size: 20 }),
  uploadImage: vi.fn(), reworkTask: vi.fn(), getTaskSegments: vi.fn().mockResolvedValue([]),
}));
vi.mock('../api/imageEdit', () => ({ editImage: vi.fn() }));
vi.mock('../api/candidateQc', async (io) => ({
  ...(await io<typeof import('../api/candidateQc')>()), candidateQc: vi.fn().mockResolvedValue(null),
}));
vi.mock('../api/canvas', async (io) => ({
  ...(await io<typeof import('../api/canvas')>()),
  listProjects: vi.fn(), getProject: vi.fn(),
  saveProject: vi.fn().mockResolvedValue({ conflict: false, canvas: { id: 1, version: 1 } }),
  createProject: vi.fn(), deleteProject: vi.fn(), getCanvasVersion: vi.fn().mockResolvedValue(1),
}));
vi.mock('../api/promptRecompose', () => ({ recomposePrompt: vi.fn(), recomposePrompts: vi.fn() }));

beforeAll(() => {
  class RO { observe() {} unobserve() {} disconnect() {} }
  if (typeof globalThis.ResizeObserver === 'undefined') (globalThis.ResizeObserver = RO as never);
});

const DIALOGUE = '今晚的海面，好像藏着什么。';

/** 带台词的一张图片节点 + 成片节点（画布节点 data 的真实形状）。 */
const nodesJson = JSON.stringify({
  nodes: [
    {
      id: 'img0', type: 'imageNode', position: { x: 100, y: 60 },
      data: {
        prompt: '陈浔站在海边灯塔前', imageUrl: 'https://cdn.local/frame.png', ratio: '16:9',
        dialogue: DIALOGUE, dialogueSpeaker: '陈浔',
      },
    },
    { id: 'compose', type: 'videoNode', position: { x: 700, y: 200 }, data: { seconds: 5 } },
  ],
  edges: [{ id: 'e0', source: 'img0', target: 'compose' }],
});

describe('台词随段提交', () => {
  beforeEach(() => {
    vi.mocked(listProjects).mockResolvedValue([
      { id: 9, name: '带台词的画布', nodesJson, edgesJson: null, version: 3 } as never,
    ]);
    vi.mocked(getProject).mockResolvedValue({
      id: 9, name: '带台词的画布', nodesJson, edgesJson: null, version: 3,
    } as never);
    vi.mocked(createVideoTask).mockResolvedValue({ id: 5 } as never);
    vi.mocked(uploadImage).mockResolvedValue({ url: 'https://cdn.local/f.png', name: 'f.png' } as never);
    vi.mocked(getTask).mockResolvedValue({ id: 5, status: 'completed' } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it('节点 data.dialogue 会进 segments[].dialogue（agent 据此逐字写进视频提示词）', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/canvas?project=9']}>
          <ImageVideoPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByTitle(/切换画布项目/);
    // 等画布节点加载进来（提示词出现即说明节点已渲染）
    await screen.findByDisplayValue(/陈浔站在海边灯塔前/);

    fireEvent.click(screen.getByRole('button', { name: '生成成片' }));
    await waitFor(() => expect(vi.mocked(createVideoTask)).toHaveBeenCalled());

    const segs = JSON.parse(String(vi.mocked(createVideoTask).mock.calls[0][0].segments));
    expect(segs.length).toBeGreaterThanOrEqual(1);
    expect(segs[0].dialogue).toBe(DIALOGUE);
    expect(segs[0].dialogue_speaker).toBe('陈浔');
  }, 40_000);
});
