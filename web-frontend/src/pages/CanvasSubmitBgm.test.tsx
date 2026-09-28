/**
 * 背景音乐开关（BGM）—— 全链路载荷护栏。
 *
 * ★ 2026-09-24 加这个开关的原因（实测，非推断）：
 *   - agnes 的视频产物**一律自带音轨**（ffprobe：h264 + aac）；
 *   - 而我们的视频提示词里**一个字的声音指令都没有** ⇒ 每段 BGM 由模型自由发挥、
 *     段段不同，拼接（acrossfade）救不了「每段换一首曲子」。
 *   官方提示词指南（Agnes Video 2.5 模板指南 §2.3）：不想要背景音乐**必须明确写**。
 *
 * 观测量：`createVideoTask` 载荷的 `bgm` 字段（默认 false = 提示词里写明排除）。
 * agent 侧断言见 agent-service/tests/test_video_prompt_spec.py。
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
  if (typeof globalThis.ResizeObserver === 'undefined') (globalThis as any).ResizeObserver = RO;
});

const proj = (id: number, name: string) => ({ id, name, nodesJson: null, edgesJson: null, version: 1 } as never);

/** 渲染画布页、给图片节点配一张图 + 填提示词（与 CanvasSubmitAnchorRefs.test.tsx 同一套最小准备）。 */
async function renderCanvasWithOneShot() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/canvas']}>
        <ImageVideoPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTitle(/切换画布项目/);
  const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(fileInput, { target: { files: [new File(['x'], 'a.png', { type: 'image/png' })] } });
  await waitFor(() => expect(vi.mocked(uploadImage)).toHaveBeenCalled());
  screen.queryAllByPlaceholderText(/本段描述/).forEach((el) =>
    fireEvent.change(el, { target: { value: '陈浔站在山坡上' } }));
}

describe('背景音乐开关', () => {
  beforeEach(() => {
    vi.mocked(listProjects).mockResolvedValue([proj(1, '长生烬9-17-4')]);
    vi.mocked(getProject).mockImplementation(async (id: number) => proj(id, '长生烬9-17-4'));
    vi.mocked(createVideoTask).mockResolvedValue({ id: 5 } as never);
    vi.mocked(uploadImage).mockResolvedValue({ url: 'https://cdn.local/f.png', name: 'f.png' } as never);
    vi.mocked(getTask).mockResolvedValue({ id: 5, status: 'completed' } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it('默认不勾：载荷里 bgm=false（agent 侧据此写「不要额外添加背景音乐」）', async () => {
    await renderCanvasWithOneShot();

    fireEvent.click(screen.getByRole('button', { name: '生成成片' }));
    await waitFor(() => expect(vi.mocked(createVideoTask)).toHaveBeenCalled());

    expect(vi.mocked(createVideoTask).mock.calls[0][0].bgm).toBe(false);
  }, 40_000);

  it('勾选后：载荷里 bgm=true（交给模型自行配乐）', async () => {
    await renderCanvasWithOneShot();

    // 开关在「精细控制」面板里（默认折叠）
    fireEvent.click(screen.getByRole('button', { name: /精细控制/ }));
    const checkbox = screen.getByLabelText(/背景音乐/) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: '生成成片' }));
    await waitFor(() => expect(vi.mocked(createVideoTask)).toHaveBeenCalled());

    expect(vi.mocked(createVideoTask).mock.calls[0][0].bgm).toBe(true);
  }, 40_000);
});
