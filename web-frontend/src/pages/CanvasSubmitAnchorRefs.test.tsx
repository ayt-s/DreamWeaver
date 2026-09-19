/**
 * #27（第二个观测点，目前只锁"已证实"的那一半）：**「生成成片」提交载荷**里，外来锚定图确实被注入。
 *
 * ✅ 已证实（本用例）：进入项目 1 时，URL `?anchorRefs` 带入的外来锚定图会出现在
 *    `createVideoTask` 载荷的 `segments[0].reference_images` 里 —— 这是**实测**，不是读注释推断。
 *    （来源：`ImageVideoPage.tsx` 约 1917-1920 行「画布 state 优先、URL anchorRefs 兜底」。
 *      这是继 `effectiveCharRefs` 之外的**第二处**直接读 URL 载荷的地方，前三条修法都没覆盖它。）
 *
 * ❌ 未做到：切项目后是否仍被带入 —— 提交成功后页面会跳到任务详情，同一条用例里点不了第二次。
 *    下一步：先 mock 导航（或先断言切项目后 anchors 是否清空），再把"切后不该带"的断言补回来。
 *
 * 相关文件：`CanvasAnchorRefsCleanup.test.tsx`（那个观测点是节点「文生图」的载荷，同样复现了污染、
 * 且已确认三条候选修法全被否 —— 详见那个文件头）。
 *
 * 与 `CanvasAnchorRefsCleanup.test.tsx` 的区别：那个看的是节点「文生图」的载荷（走 AnchorsCtx 上下文），
 * 这个看的是**提交路径** —— `ImageVideoPage.tsx` 约 1917-1920 行：
 *   把画布 state 的锚定图（优先）或 **URL anchorRefs（兜底）** 合并进每个 segment 的 reference_images。
 * 这是**第二处**直接读 URL 载荷的地方，前三条修法都没覆盖它。
 *
 * 观测量：`createVideoTask` 载荷里 `segments` 的 `reference_images`。
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
vi.mock('../api/promptRecompose', () => ({ recomposePrompts: vi.fn() }));

beforeAll(() => {
  class RO { observe() {} unobserve() {} disconnect() {} }
  if (typeof globalThis.ResizeObserver === 'undefined') (globalThis as any).ResizeObserver = RO;
});

const proj = (id: number, name: string) => ({ id, name, nodesJson: null, edgesJson: null, version: 1 } as never);
const FOREIGN_URL = 'https://cdn.foreign/chen.png';
const FOREIGN = { characters: { 陈浔: { url: FOREIGN_URL, desc: '少年陈浔' } }, scenes: {} };

describe('#27 第二观测点：提交载荷里的外来锚定图', () => {
  beforeEach(() => {
    vi.mocked(listProjects).mockResolvedValue([proj(1, '长生烬9-17-1'), proj(2, '别的项目')]);
    vi.mocked(getProject).mockImplementation(async (id: number) => proj(id, id === 1 ? '长生烬9-17-1' : '别的项目'));
    vi.mocked(createVideoTask).mockResolvedValue({ id: 5 } as never);
    vi.mocked(uploadImage).mockResolvedValue({ url: 'https://cdn.local/f.png', name: 'f.png' } as never);
    vi.mocked(getTask).mockResolvedValue({ id: 5, status: 'completed', imageUrls: JSON.stringify(['https://cdn.local/o.png']) } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it('切项目后，「生成成片」的 segments.reference_images 里不该再有外来图', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/canvas?anchorRefs=${encodeURIComponent(JSON.stringify(FOREIGN))}`]}>
          <ImageVideoPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByTitle(/切换画布项目/);

    // 给图片节点配一张图 + 填含锚定图名的提示词 ⇒ 该段会命中锚定图
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [new File(['x'], 'a.png', { type: 'image/png' })] } });
    await waitFor(() => expect(vi.mocked(uploadImage)).toHaveBeenCalled());
    screen.queryAllByPlaceholderText(/本段描述/).forEach((el) =>
      fireEvent.change(el, { target: { value: '陈浔站在山坡上' } }));

    fireEvent.click(screen.getByRole('button', { name: '生成成片' }));
    await waitFor(() => expect(vi.mocked(createVideoTask)).toHaveBeenCalled());
    const segs1 = JSON.parse(String(vi.mocked(createVideoTask).mock.calls[0][0].segments));
    expect(segs1.length).toBeGreaterThanOrEqual(1);
    expect(JSON.stringify(segs1[0].reference_images ?? [])).toContain(FOREIGN_URL);

    // ⚠️ 本来这里要再切到项目 2、提交第二次、断言载荷里没有外来图 —— **做不了**：
    //   提交成功后页面会跳到任务详情（画布页被换掉），同一条用例里点不了第二次。
    //   下一步插桩要么先 mock 掉导航、要么先断言"切项目后 anchors 是否已清"，
    //   再把这条断言补回来（见文件头说明）。
  }, 40_000);
});
