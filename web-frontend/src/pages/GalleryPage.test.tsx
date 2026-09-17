import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import BrowseGalleryPage from '../pages/GalleryPage';
import { listTasks } from '../api/tasks';
import type { TaskListResponse, TaskResponse } from '../types/task';

// 只替换列表取数：搜索/开关这类「发什么参数」的问题，看调用参数最直接。
// （默认实现是真发 XHR，在 jsdom 里必失败，页面会渲染成加载失败态，测不了筛选。）
vi.mock('../api/tasks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tasks')>();
  return { ...actual, listTasks: vi.fn() };
});

function rows(items: Array<{ id: number; prompt: string }>): TaskListResponse {
  return {
    list: items.map(
      (i) =>
        ({
          id: i.id,
          prompt: i.prompt,
          status: 'completed',
          genType: 'text_video',
          isDraft: true,
        }) as unknown as TaskResponse,
    ),
    total: items.length,
    page: 1,
    size: 6,
  };
}

function renderGallery(entry = '/gallery') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <BrowseGalleryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 最近一次取数的参数（搜索这类断言看它最准） */
function lastParams() {
  const calls = vi.mocked(listTasks).mock.calls;
  return calls[calls.length - 1]?.[0];
}

describe('GalleryPage 搜索与素材开关', () => {
  beforeEach(() => {
    vi.mocked(listTasks).mockReset();
    vi.mocked(listTasks).mockResolvedValue(rows([{ id: 1, prompt: '初音未来跨屏' }]));
  });

  it('标题渲染', async () => {
    renderGallery();
    expect(screen.getByText('作品画廊')).toBeInTheDocument();
    // 没搜索时 keyword 为空串（页面 state 直接透传，由 api 层决定要不要带这个参数）
    await waitFor(() => expect(lastParams()?.keyword).toBeFalsy());
  });

  it('搜索框防抖后才取数，且只打一次后端（不是每敲一个字都请求）', async () => {
    renderGallery();
    await waitFor(() => expect(vi.mocked(listTasks).mock.calls.length).toBeGreaterThan(0));
    const before = vi.mocked(listTasks).mock.calls.length;

    const input = screen.getByLabelText('搜索作品');
    fireEvent.change(input, { target: { value: '初' } });
    fireEvent.change(input, { target: { value: '初音' } });
    fireEvent.change(input, { target: { value: '初音未来' } });

    await waitFor(() => expect(lastParams()?.keyword).toBe('初音未来'), { timeout: 2000 });
    // 三次输入只应产生一次额外请求（防抖生效）
    expect(vi.mocked(listTasks).mock.calls.length).toBe(before + 1);
  });

  it('搜不到时给的是「没搜到」，不是「还没有历史作品」', async () => {
    vi.mocked(listTasks).mockImplementation(async (p) =>
      p?.keyword ? rows([]) : rows([{ id: 1, prompt: '初音未来跨屏' }]),
    );
    renderGallery();
    await waitFor(() => expect(screen.getByText('初音未来跨屏')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('搜索作品'), { target: { value: '不存在的词' } });

    await waitFor(() => expect(screen.getByText(/没搜到「不存在的词」/)).toBeInTheDocument());
    expect(screen.queryByText('还没有历史作品')).toBeNull();
    expect(screen.queryByText('该筛选下暂无作品')).toBeNull();
  });

  it('?assets=1 直接打开「显示画布素材」（画布面板「去画廊看全部」的落点）', async () => {
    renderGallery('/gallery?assets=1');
    await waitFor(() => expect(lastParams()?.includeAssets).toBe(true));
    expect(screen.getByText('隐藏画布素材')).toBeInTheDocument();
  });
});
