import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import ImageVideoPage from './ImageVideoPage';
import { createVideoTask } from '../api/tasks';

// 只替换「提交任务」这一个函数：断言单节点「文生图」确实走了直出短路。
// 页面还从同一模块拿别的函数，所以用 importOriginal 保留其余实现。
vi.mock('../api/tasks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tasks')>();
  return {
    ...actual,
    // 让它立刻失败：本用例只关心**调用参数**，不需要跑完轮询
    createVideoTask: vi.fn().mockRejectedValue(new Error('用例到此为止')),
  };
});

// React Flow 在 jsdom 下需要 ResizeObserver（白屏回归防护：保证页面无运行时错误挂载）
beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  // 注入 jsdom 缺失的全局（新版 jsdom 已自带则无需）
  if (typeof globalThis.ResizeObserver === 'undefined') {
    (globalThis as unknown as Record<string, unknown>).ResizeObserver = RO;
  }
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ImageVideoPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** React Flow 把节点定位写成 translate(xpx, ypx) —— 成片顺序的真实来源就是 x。 */
function xOf(el: Element): number {
  const m = /translate\(([-\d.]+)px/.exec((el as HTMLElement).style.transform);
  return m ? Number(m[1]) : NaN;
}

/** 画布上的图片节点（有「第 N 段」徽标的那些）：id / x / 段号 / DOM。 */
function shots() {
  return Array.from(document.querySelectorAll('[data-testid^="rf__node-"]'))
    .map((el) => ({
      id: (el.getAttribute('data-testid') || '').replace('rf__node-', ''),
      x: xOf(el),
      order: Number(/第 (\d+) 段/.exec(el.textContent || '')?.[1] ?? 0),
      el,
    }))
    .filter((n) => n.order > 0);
}

function moveBtn(el: Element, arrow: string) {
  return Array.from(el.querySelectorAll('button')).find((b) => b.textContent === arrow)!;
}

describe('ImageVideoPage 无限画布页', () => {
  it('挂载渲染成功（标题/节点类型/素材来源/比例/提交按钮齐全）', () => {
    renderPage();
    // 顶栏标题
    expect(screen.getAllByText((t) => t.includes('无限画布')).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText((t) => t.includes('图生视频')).length).toBeGreaterThanOrEqual(1);
    // 左侧添加节点（按钮 + 画布内节点卡同名，允许重复）
    expect(screen.getAllByText('文本节点').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('图片节点').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('成片节点')).toBeInTheDocument();
    // 素材来源
    expect(screen.getByText('本地上传')).toBeInTheDocument();
    expect(screen.getByText((t) => t.includes('从历史作品选取'))).toBeInTheDocument();
    // 初版画布节点
    expect(screen.getAllByText('文本节点').length).toBeGreaterThanOrEqual(1);
    // 文本节点的「模式下拉」已按设计**移除**（四项都是 disabled 占位，属过度设计），
    // 现在是 textarea + AI 生成/改写；`data.mode` 只为兼容老画布数据保留，
    // 已不是可见文案 —— 见 ImageVideoPage.tsx:117-119。
    // 所以这里锁**新契约**（而不是删掉断言）：改坏了下拉会回来、或 AI 入口丢了都会红。
    expect(screen.getByPlaceholderText(/描述画面内容/)).toBeInTheDocument();
    // ⚠️ 不要用 `getByRole('button', { name: 'AI' })`：这个按钮带 `title`，
    //    可访问名的计算在 title/内容之间不稳定，改用可见文本更稳。
    expect(screen.getAllByText('AI').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('自己编写')).toBeNull();
    // 比例预设 + 底部模型选择 + 提交按钮
    expect(screen.getAllByText('16:9').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('视频模型')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成成片' })).toBeInTheDocument();
  });

  it('分镜 ▲▼ 真能换位（P4 接线：点击 → 换 x → 徽标互换）', () => {
    renderPage();
    const add = () => screen.getAllByRole('button', { name: '图片节点' })[0];
    for (let i = 0; i < 3 && shots().length < 3; i += 1) fireEvent.click(add());

    const before = shots();
    expect(before.length).toBeGreaterThanOrEqual(3);
    const first = before.find((n) => n.order === 1)!;
    const second = before.find((n) => n.order === 2)!;

    fireEvent.click(moveBtn(first.el, '▼'));

    const after = shots();
    expect(after.find((n) => n.id === first.id)!.order).toBe(2);
    expect(after.find((n) => n.id === second.id)!.order).toBe(1);
    // x 归一化成 base + k*340（与后端 reorder_shots 同口径）
    const sorted = [...after].sort((a, b) => a.x - b.x);
    sorted.forEach((n, k) => expect(n.x).toBeCloseTo(sorted[0].x + k * 340, 3));
  });

  it('分镜 ▲▼ 边界禁用态正确（第 1 段 ▲ 灰 / 最后一段 ▼ 灰）', () => {
    renderPage();
    const add = () => screen.getAllByRole('button', { name: '图片节点' })[0];
    for (let i = 0; i < 3 && shots().length < 3; i += 1) fireEvent.click(add());

    const list = shots();
    const first = list.find((n) => n.order === 1)!;
    const last = list.find((n) => n.order === list.length)!;
    expect(moveBtn(first.el, '▲')).toBeDisabled();
    expect(moveBtn(first.el, '▼')).not.toBeDisabled();
    expect(moveBtn(last.el, '▼')).toBeDisabled();
  });

  it('★ 单节点「文生图」必须走直出短路（directImage=true）', async () => {
    renderPage();
    // 初始画布的图片节点没有提示词 —— 先填上，否则按钮只会提示「请先填写提示词」
    const desc = screen.getByPlaceholderText(/本段描述/) as HTMLTextAreaElement;
    fireEvent.change(desc, { target: { value: '陈浔在洞口整理草药' } });

    const btn = screen
      .getAllByText('文生图')
      .map((el) => el.closest('button'))
      .find(Boolean);
    expect(btn).toBeTruthy();
    fireEvent.click(btn as HTMLButtonElement);

    await waitFor(() => expect(vi.mocked(createVideoTask)).toHaveBeenCalled());
    // 直接用真实类型（CreateTaskRequest 本来就有 genType / directImage，不必断言成 Record）
    const arg = vi.mocked(createVideoTask).mock.calls[0][0];

    expect(arg.genType).toBe('text_image');
    // ⚠️ 核心断言：不传 directImage 时 Java 不加 `direct_image`，agent 会**按 prompt 重新拆镜**
    // → 一次白出 3~5 张不同画面的图，而这里只用得上第 1 张。
    // 这条就是 2026-09-17 修的那个额度浪费 bug 的回归护栏（TaskServiceImpl.java:380-383）。
    expect(arg.directImage).toBe(true);
  });
});