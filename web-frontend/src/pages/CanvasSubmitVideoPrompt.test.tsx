/**
 * 画布节点「视频提示词」编辑框随段提交 —— 载荷护栏（★ 2026-09-24）。
 *
 * 背景：视频提示词此前是**提交时现生成**的，用户既看不到也改不了
 * （节点 `data.prompt` 存的是图像提示词）。现在节点上有独立的「视频提示词」框：
 * 留空 = 由本段描述自动改写；填了 = 以它为准改写（agent 侧断言见
 * agent-service/tests/test_video_prompt_spec.py 的 custom_video_prompt 两条）。
 *
 * 观测量：`createVideoTask` 载荷 `segments[0].video_prompt_cn`。
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

const nodesJson = JSON.stringify({
  nodes: [
    {
      id: 'img0', type: 'imageNode', position: { x: 100, y: 60 },
      data: {
        prompt: '陈浔站在海边灯塔前', imageUrl: 'https://cdn.local/frame.png', ratio: '16:9',
      },
    },
    { id: 'compose', type: 'videoNode', position: { x: 700, y: 200 }, data: { seconds: 5 } },
  ],
  edges: [{ id: 'e0', source: 'img0', target: 'compose' }],
});

async function renderCanvasWithOneShot() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/canvas?project=9']}>
        <ImageVideoPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTitle(/切换画布项目/);
  await screen.findByDisplayValue(/陈浔站在海边灯塔前/);
}

describe('画布「视频提示词」编辑框', () => {
  beforeEach(() => {
    vi.mocked(listProjects).mockResolvedValue([
      { id: 9, name: '带视频提示词的画布', nodesJson, edgesJson: null, version: 3 } as never,
    ]);
    vi.mocked(getProject).mockResolvedValue({
      id: 9, name: '带视频提示词的画布', nodesJson, edgesJson: null, version: 3,
    } as never);
    vi.mocked(createVideoTask).mockResolvedValue({ id: 5 } as never);
    vi.mocked(uploadImage).mockResolvedValue({ url: 'https://cdn.local/f.png', name: 'f.png' } as never);
    vi.mocked(getTask).mockResolvedValue({ id: 5, status: 'completed' } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it('留空：载荷里不带 video_prompt_cn（保持「由本段描述改写」的原行为）', async () => {
    await renderCanvasWithOneShot();

    fireEvent.click(screen.getByRole('button', { name: '生成成片' }));
    await waitFor(() => expect(vi.mocked(createVideoTask)).toHaveBeenCalled());

    const segs = JSON.parse(String(vi.mocked(createVideoTask).mock.calls[0][0].segments));
    expect(segs[0].video_prompt_cn).toBeUndefined();
  }, 40_000);

  it('填了：载荷带 video_prompt_cn（agent 以它为准改写）', async () => {
    await renderCanvasWithOneShot();

    // 折叠的 details 里子节点仍在 DOM 中，可直接定位输入框
    const box = screen.getByPlaceholderText(/留空 = 用上面的描述/);
    fireEvent.change(box, { target: { value: '写实电影质感，男子缓步走向灯塔，一镜到底。' } });
    // 摘要文案跟着变（用户能看到「已自定义」这个状态）
    expect(screen.getByText(/视频提示词（已自定义）/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '生成成片' }));
    await waitFor(() => expect(vi.mocked(createVideoTask)).toHaveBeenCalled());

    const segs = JSON.parse(String(vi.mocked(createVideoTask).mock.calls[0][0].segments));
    expect(segs[0].video_prompt_cn).toBe('写实电影质感，男子缓步走向灯塔，一镜到底。');
  }, 40_000);
});
