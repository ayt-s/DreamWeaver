/**
 * #28：单节点「文生图」90 秒轮询到期**必须有终态**（2026-09-19 修）。
 *
 * 缺陷：轮询是 `while (Date.now() - t0 < 90_000)`，已有 completed / failed / expired /
 * interrupted 四个出口，但**到期时静默退出** ⇒ 状态永远停在「文生图进行中…」
 * （排队 + 出图经常超过 90 秒）⇒ 用户以为卡死，往往会再点一次 ⇒ 重复出图白花钱。
 *
 * 本用例让任务一直停在 pending，推进假时钟越过 90 秒，断言**出现了明确的超时文案**。
 * ※ 单独成文件（不并进 ImageVideoPage.test.tsx）：那边用例多、mock 计数互相影响，
 *   上次插进去一条就把邻近用例的 `mock.calls[0]` 挤掉了。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// React Flow 在 jsdom 下需要 ResizeObserver（另一份用例里的同款注入，缺了会直接白屏报错）
beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  if (typeof globalThis.ResizeObserver === 'undefined') {
    (globalThis as unknown as Record<string, unknown>).ResizeObserver = RO;
  }
});

import ImageVideoPage from './ImageVideoPage';
import { createVideoTask, getTask, listTasks, uploadImage } from '../api/tasks';

vi.mock('../api/tasks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tasks')>();
  return {
    ...actual,
    createVideoTask: vi.fn(),
    getTask: vi.fn(),
    listTasks: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, size: 20 }),
    uploadImage: vi.fn(),
    reworkTask: vi.fn(),
    getTaskSegments: vi.fn().mockResolvedValue([]),
  };
});
vi.mock('../api/imageEdit', () => ({ editImage: vi.fn() }));
vi.mock('../api/candidateQc', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/candidateQc')>();
  return { ...actual, candidateQc: vi.fn().mockResolvedValue(null) };
});
vi.mock('../api/canvas', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/canvas')>();
  return {
    ...actual,
    saveProject: vi.fn().mockResolvedValue({ conflict: false, canvas: { id: 1, version: 1 } }),
    getProject: vi.fn().mockResolvedValue({ id: 1, name: 'x', nodesJson: null, edgesJson: null, version: 0 }),
  };
});
vi.mock('../api/promptRecompose', () => ({ recomposePrompts: vi.fn() }));

beforeEach(() => {
  vi.mocked(uploadImage).mockResolvedValue({ url: 'https://cdn.local/f.png', name: 'f.png' } as never);
  vi.mocked(createVideoTask).mockResolvedValue({ id: 77 } as never);
  vi.mocked(listTasks).mockResolvedValue({ items: [], total: 0, page: 1, size: 20 } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('单节点「文生图」的轮询到期（#28）', () => {
  it('任务一直停在 pending：到 90 秒必须有明确终态，而不是永远「进行中…」', async () => {
    // 假时钟：轮询循环里是 `await new Promise(r => setTimeout(r, 4000))` + `Date.now()`，
    // 两者都要被接管才能把 90 秒“快进”出来（vitest 的假时钟默认连 Date 一起接管）。
    vi.useFakeTimers();
    // 一直“排队中”：既不是 completed，也不是 failed/expired/interrupted
    vi.mocked(getTask).mockResolvedValue({ id: 77, status: 'pending' } as never);

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/canvas']}>
          <ImageVideoPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // 填提示词 → 点节点上的「文生图」
    const desc = screen.getAllByPlaceholderText(/本段描述/)[0] as HTMLTextAreaElement;
    fireEvent.change(desc, { target: { value: '陈浔站在山坡上' } });
    const btn = screen
      .getAllByText('文生图')
      .map((el) => el.closest('button'))
      .find(Boolean) as HTMLButtonElement;
    await act(async () => {
      fireEvent.click(btn);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(vi.mocked(createVideoTask)).toHaveBeenCalled();

    // 推进到接近 90 秒
    await act(async () => {
      await vi.advanceTimersByTimeAsync(88_000);
    });
    // 此时仍在「进行中」—— 这是既定行为（90 秒上限本身没改）
    expect(screen.queryByText(/生成超时/)).toBeNull();

    // 越过 90 秒 → 必须出现终态文案（这就是 #28 的修复点）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_000);
    });
    expect(screen.getByText(/生成超时/)).toBeInTheDocument();
  }, 20_000);
});
