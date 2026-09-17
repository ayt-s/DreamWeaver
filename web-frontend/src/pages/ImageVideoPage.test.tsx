import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import ImageVideoPage from './ImageVideoPage';
import { createVideoTask, getTask, listTasks } from '../api/tasks';
import type { TaskListResponse, TaskResponse } from '../types/task';

// 只替换「提交任务」这一个函数：断言单节点「文生图」确实走了直出短路。
// 页面还从同一模块拿别的函数，所以用 importOriginal 保留其余实现。
vi.mock('../api/tasks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tasks')>();
  return {
    ...actual,
    // 让它立刻失败：本用例只关心**调用参数**，不需要跑完轮询
    createVideoTask: vi.fn().mockRejectedValue(new Error('用例到此为止')),
    // 「从历史作品选取」的取数：默认返回空（用例里按需 mockResolvedValue/mockRejectedValue）
    listTasks: vi.fn(),
    getTask: vi.fn(),
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

/** 构造「历史作品」分页响应：只填页面真正读的字段（status / prompt / imageUrls） */
function historyList(
  rows: Array<{ id: number; prompt: string; urls: string[]; status?: string }>,
  total = rows.length,
): TaskListResponse {
  return {
    list: rows.map(
      (r) =>
        ({
          id: r.id,
          prompt: r.prompt,
          status: r.status ?? 'completed',
          genType: 'text_image',
          imageUrls: JSON.stringify(r.urls),
        }) as unknown as TaskResponse,
    ),
    total,
    page: 1,
    size: rows.length,
  };
}

describe('「从历史作品选取」面板', () => {
  beforeEach(() => {
    vi.mocked(listTasks).mockReset();
    // 用例要改行为的（批量文生图）自己 mockResolvedValue；这里先恢复默认「提交即失败」
    vi.mocked(createVideoTask).mockReset().mockRejectedValue(new Error('用例到此为止'));
    vi.mocked(getTask).mockReset();
  });

  it('没图的任务不占格；同提示词重跑各自出一格（两组候选都要能挑）', async () => {
    vi.mocked(listTasks).mockResolvedValue(
      historyList([
        {
          id: 78,
          prompt: '陈浔闻焦味',
          urls: [
            'https://cdn.agnes-ai.space/a1.png',
            'https://cdn.agnes-ai.space/a2.png',
            'https://cdn.agnes-ai.space/a3.png',
          ],
        },
        { id: 77, prompt: '陈浔闻焦味', urls: ['https://cdn.agnes-ai.space/old1.png'] },
        { id: 30, prompt: '初音未来跨屏', urls: ['https://cdn.agnes-ai.space/c1.png'] },
        { id: 29, prompt: '没图的任务', urls: [] },
      ]),
    );
    renderPage();

    await waitFor(() => expect(screen.getAllByAltText('陈浔闻焦味')).toHaveLength(2));
    // 同一提示词的两次生成各有自己的候选 → 不合并（合并等于替用户丢掉一组候选）
    expect(screen.getByAltText('初音未来跨屏')).toBeInTheDocument();
    // 没图的任务以前占一格却点不动（渲染成空洞）
    expect(screen.queryByAltText('没图的任务')).toBeNull();
    // 候选数角标：让用户知道这一格背后还有两张可挑
    expect(screen.getByText(/3\s*张/)).toBeInTheDocument();
    // 取数参数：后端过滤（status/source）——以前是前端取 40 条再自己筛，
    // 排队/失败任务会挤掉名额，且素材会把作品挤出面板
    expect(vi.mocked(listTasks).mock.calls[0][0]).toMatchObject({
      genType: 'text_image',
      status: 'completed',
      source: 'asset',
      includeAssets: true,
      page: 1,
      size: 12,
    });
  });

  it('切「作品」栏 → 用 source=work 重新取数（素材与作品不再混排）', async () => {
    vi.mocked(listTasks).mockImplementation(async (p) =>
      p?.source === 'work'
        ? historyList([
            { id: 30, prompt: '初音未来跨屏', urls: ['https://cdn.agnes-ai.space/c1.png'] },
          ])
        : historyList([
            { id: 78, prompt: '陈浔闻焦味', urls: ['https://cdn.agnes-ai.space/a1.png'] },
          ]),
    );
    renderPage();

    await waitFor(() => expect(screen.getByAltText('陈浔闻焦味')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '作品' }));

    await waitFor(() => expect(screen.getByAltText('初音未来跨屏')).toBeInTheDocument());
    const calls = vi.mocked(listTasks).mock.calls;
    expect(calls[calls.length - 1]?.[0]).toMatchObject({ source: 'work' });
    // 换栏后素材那张不该还留在网格里（placeholderData 只是防闪空，不应留下旧源的行）
    await waitFor(() => expect(screen.queryByAltText('陈浔闻焦味')).toBeNull());
  });

  it('「加载更多」按页累加请求（不再硬截断在 12 格）', async () => {
    // total 固定 26（模拟库里共有 26 张），list 按请求的 size 返回前 N 张
    vi.mocked(listTasks).mockImplementation(async (p) =>
      historyList(
        Array.from({ length: 26 }, (_, i) => ({
          id: 100 - i,
          prompt: `镜头 ${i + 1}`,
          urls: [`https://cdn.agnes-ai.space/s${i + 1}.png`],
        })).slice(0, p?.size ?? 12),
        26,
      ),
    );
    renderPage();

    // 第一次：size=12
    await waitFor(() => expect(vi.mocked(listTasks).mock.calls[0][0]).toMatchObject({ size: 12 }));
    await waitFor(() => expect(screen.getAllByAltText(/^镜头 /)).toHaveLength(12));
    const more = screen.getByRole('button', { name: /加载更多/ });
    // total=26 > 已显示 12 → 按钮出现并带着真实总数
    expect(more.textContent).toContain('共 26');

    fireEvent.click(more);
    const afterClick = vi.mocked(listTasks).mock.calls;
    await waitFor(() =>
      expect(afterClick[afterClick.length - 1]?.[0]).toMatchObject({ size: 24 }),
    );
    await waitFor(() => expect(screen.getAllByAltText(/^镜头 /)).toHaveLength(24));
    // 还剩 2 张没显示 → 按钮仍在（下一次会顶到 48 上限）
    expect(screen.getByRole('button', { name: /加载更多/ }).textContent).toContain('已显示 24');
  });

  it('两个栏的空态文案不同（作品栏要说清「素材不在这里」）', async () => {
    vi.mocked(listTasks).mockResolvedValue(historyList([]));
    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/还没有画布素材/)).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: '作品' }));
    await waitFor(() => expect(screen.getByText(/还没有作品/)).toBeInTheDocument());
    expect(screen.getByText(/画布素材不在这里/)).toBeInTheDocument();
  });

  it('加载中不显示空态（假空态会让用户以为作品丢了）', async () => {
    let release: (v: TaskListResponse) => void = () => {};
    vi.mocked(listTasks).mockReturnValue(
      new Promise<TaskListResponse>((r) => {
        release = r;
      }),
    );
    renderPage();

    // 网格里一处 + chip 旁的「加载中…」提示（isFetching），两处都算数
    expect(screen.getAllByText('加载中…').length).toBeGreaterThan(0);
    expect(screen.queryByText(/还没有画布素材/)).toBeNull();
    expect(screen.queryByText(/暂无历史作品/)).toBeNull();

    release(
      historyList([{ id: 1, prompt: '雪山日出', urls: ['https://cdn.agnes-ai.space/s1.png'] }]),
    );
    await waitFor(() => expect(screen.getByAltText('雪山日出')).toBeInTheDocument());
    expect(screen.queryByText('加载中…')).toBeNull();
  });

  it('加载失败显示「加载失败 + 重试」，而不是「暂无历史作品」', async () => {
    vi.mocked(listTasks).mockRejectedValue(new Error('后台挂了'));
    renderPage();

    await waitFor(() => expect(screen.getByText(/历史作品加载失败/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    // 失败态也绝不能退化成空态文案（用户会以为作品没了、跑去修错的地方）
    expect(screen.queryByText(/还没有画布素材/)).toBeNull();
    expect(screen.queryByText(/暂无历史作品/)).toBeNull();
  });

  it('点击作品入画布时把候选带进节点（否则画布上永远只拿得到第 1 张）', async () => {
    vi.mocked(listTasks).mockResolvedValue(
      historyList([
        {
          id: 5,
          prompt: '雪山日出',
          urls: [
            'https://cdn.agnes-ai.space/s1.png',
            'https://cdn.agnes-ai.space/s2.png',
            'https://cdn.agnes-ai.space/s3.png',
          ],
        },
      ]),
    );
    renderPage();

    fireEvent.click(await screen.findByAltText('雪山日出'));
    // 节点内既有的候选切换器此时才有内容可切
    await waitFor(() => expect(screen.getByText(/候选 3 张/)).toBeInTheDocument());
  });

  it('入画布的新节点落在当前视口中心，不是写死的 (380, 420+n*40)', async () => {
    vi.mocked(listTasks).mockResolvedValue(
      historyList([{ id: 5, prompt: '雪山日出', urls: ['https://cdn.agnes-ai.space/s1.png'] }]),
    );
    const { container } = renderPage();
    const hit = await screen.findByAltText('雪山日出');

    // jsdom 里所有 getBoundingClientRect 都是 0×0 → spawnPosition 会走兜底分支（= 测不到接线）。
    // 这里给画布一个真实尺寸：视口中心 (500,300) 落到节点左上角 ≈ (368,180)，
    // 而写死的兜底值是 (380, 420+3*40=540) —— 两者 y 差 350 多，足以分辨。
    expect(container.querySelector('.react-flow__pane')).toBeTruthy();
    const rect = {
      x: 0, y: 0, left: 0, top: 0, right: 1000, bottom: 600,
      width: 1000, height: 600, toJSON: () => ({}),
    } as DOMRect;
    const spy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockReturnValue(rect);
    const yOfLast = () => {
      const nodes = Array.from(
        container.querySelectorAll('[data-testid^="rf__node-"]'),
      ) as HTMLElement[];
      const m = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(
        nodes[nodes.length - 1].style.transform,
      );
      return m ? Number(m[2]) : NaN;
    };
    const nodeCount = () => container.querySelectorAll('[data-testid^="rf__node-"]').length;
    try {
      fireEvent.click(hit);
      await waitFor(() => expect(nodeCount()).toBe(4));
      const y1 = yOfLast();
      // 同一张图再点一次：落点相同 → 必须被错开，而不是叠在上一张身上
      fireEvent.click(hit);
      await waitFor(() => expect(nodeCount()).toBe(5));
      const y2 = yOfLast();

      // 写死的兜底坐标是 (380, 420 + n*40)：连点两次会是 540 / 580（差 40）。
      // 落到视口中心时两次落在同一点 → 第二张按「节点高 240 + 16 间距」下移。
      expect(y2 - y1).toBeCloseTo(256, 0);
      expect([540, 580]).not.toContain(y1); // 确认真的没走兜底分支
    } finally {
      spy.mockRestore();
    }
  });

  it('入画布记下来源（只读）；空提示词写明兜底；点「填入提示词」才写进 prompt', async () => {
    vi.mocked(listTasks).mockResolvedValue(
      historyList([{ id: 78, prompt: '陈浔闻焦味', urls: ['https://cdn.agnes-ai.space/a1.png'] }]),
    );
    renderPage();
    fireEvent.click(await screen.findByAltText('陈浔闻焦味'));

    // 来源只读展示：源任务 id + 原文；**不自动**写进 prompt
    await waitFor(() => expect(screen.getByText(/来源 #78/)).toBeInTheDocument());
    // 空提示词的后果要说清（agent 侧兜底成「对参考图缓慢推进」）
    expect(screen.getByText(/提交时用通用运镜兜底/)).toBeInTheDocument();
    // ⚠️ 初始画布本来就有一个图片节点（占位文本相同）→ 取最后那个 = 刚从面板加进来的
    const descOf = () => {
      const all = screen.getAllByPlaceholderText(/本段描述/) as HTMLTextAreaElement[];
      return all[all.length - 1];
    };
    expect(descOf().value).toBe('');

    // 显式动作才接管：点了才填进可编辑的 prompt
    // ⚠️ 用 DOM 直查而不是 getByRole：React Flow 的节点内容不在可访问角色树里
    //   （同文件里 ▲▼ 的 moveBtn 也是这么找的）
    const fillBtn = Array.from(document.querySelectorAll('button')).find(
      (b) => b.textContent === '填入提示词',
    );
    expect(fillBtn).toBeTruthy();
    fireEvent.click(fillBtn as HTMLButtonElement);
    await waitFor(() => expect(descOf().value).toBe('陈浔闻焦味'));
    expect(screen.queryByText(/提交时用通用运镜兜底/)).toBeNull();
  });

  it('顶到 48 张上限后改为「去画廊看全部」链接（窄侧栏不继续堆长）', async () => {
    vi.mocked(listTasks).mockImplementation(async (p) =>
      historyList(
        Array.from({ length: 60 }, (_, i) => ({
          id: 200 - i,
          prompt: `镜头 ${i + 1}`,
          urls: [`https://cdn.agnes-ai.space/c${i}.png`],
        })).slice(0, p?.size ?? 12),
        60,
      ),
    );
    renderPage();

    // 12 → 24 → 36 → 48（每次点都是「再加载一页」）
    for (let i = 0; i < 3; i += 1) {
      const btn = await screen.findByRole('button', { name: /加载更多/ });
      fireEvent.click(btn);
      await waitFor(() =>
        expect(vi.mocked(listTasks).mock.calls.length).toBeGreaterThanOrEqual(i + 2),
      );
    }

    const link = await screen.findByRole('link', { name: /去画廊看全部/ });
    expect(link).toHaveAttribute('href', '/gallery?assets=1');
    expect(screen.queryByRole('button', { name: /加载更多/ })).toBeNull();
  });

  it('一键文生图跑完后面板会重新取数（新素材不用刷新页面）', async () => {
    vi.mocked(listTasks).mockResolvedValue(historyList([]));
    vi.mocked(createVideoTask).mockResolvedValue({ id: 4242 } as never);
    vi.mocked(getTask).mockResolvedValue({
      status: 'completed',
      imageUrls: JSON.stringify(['https://cdn.agnes-ai.space/new1.png']),
    } as never);

    renderPage();
    await waitFor(() => expect(screen.getByText(/还没有画布素材/)).toBeInTheDocument());
    const before = vi.mocked(listTasks).mock.calls.length;

    // 给初始画布的图片节点填提示词 → 它成为「有提示词但没图」的批量目标
    fireEvent.change(screen.getByPlaceholderText(/本段描述/), {
      target: { value: '陈浔在洞口整理草药' },
    });
    fireEvent.click(screen.getByRole('button', { name: /一键文生图/ }));

    // generateOneImage 内部是 4s 一轮的轮询（真实计时器），所以这条用例要 4~5 秒
    await waitFor(
      () => expect(vi.mocked(listTasks).mock.calls.length).toBeGreaterThan(before),
      { timeout: 20000 },
    );
  }, 30000);
});

describe('新节点落点（P1-7）', () => {
  beforeEach(() => {
    vi.mocked(listTasks).mockReset().mockResolvedValue(historyList([]));
    vi.mocked(createVideoTask).mockReset().mockRejectedValue(new Error('用例到此为止'));
    vi.mocked(getTask).mockReset();
  });

  it('拿不到视口尺寸时退回固定坐标，且新增的节点互不重叠', () => {
    /** jsdom 里 `.react-flow__pane` 的 rect 恒为 0 → `spawnPosition` 必须返回 null 走兜底
     *  （`screenToFlowPosition` 需要真实测量，拿不到就当「视口中心」不可用）。
     *  这里钉的是：① 不崩；② 连点多次不会把节点叠在同一个点上（看不见＝以为点了没反应）。
     */
    renderPage();
    const before = document.querySelectorAll('[data-testid^="rf__node-"]').length;
    const addImage = screen.getAllByRole('button', { name: '图片节点' })[0];
    fireEvent.click(addImage);
    fireEvent.click(addImage);
    fireEvent.click(addImage);

    const nodes = Array.from(document.querySelectorAll('[data-testid^="rf__node-"]'));
    expect(nodes.length).toBe(before + 3);
    const transforms = nodes.map((n) => (n as HTMLElement).style.transform);
    expect(new Set(transforms).size).toBe(transforms.length);
  });
});

describe('候选图质检打标（P0-1）', () => {
  const URLS = [
    'https://cdn.agnes-ai.space/q1.png',
    'https://cdn.agnes-ai.space/q2.png',
    'https://cdn.agnes-ai.space/q3.png',
  ];

  beforeEach(() => {
    vi.mocked(listTasks)
      .mockReset()
      .mockResolvedValue(historyList([{ id: 78, prompt: '陈浔闻焦味', urls: URLS }]));
    vi.mocked(createVideoTask).mockReset().mockRejectedValue(new Error('用例到此为止'));
    vi.mocked(getTask).mockReset();
  });

  afterEach(() => vi.unstubAllGlobals());

  /** 只拦 `/v1/qc/images`，其余请求照旧（页面还有别的取数，别一并打断）。 */
  function stubQc(payload: unknown, ok = true) {
    const real = globalThis.fetch;
    const spy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/v1/qc/images')) {
        return { ok, json: async () => payload } as unknown as Response;
      }
      return real(input, init);
    });
    vi.stubGlobal('fetch', spy);
    return spy;
  }

  it('命中红线的候选打标，并告诉用户有几张', async () => {
    const spy = stubQc({
      code: 0,
      data: {
        results: [
          { index: 0, url: URLS[0], skipped: false, closeup: false, faces: 1, faceSpan: 0.09 },
          { index: 1, url: URLS[1], skipped: false, closeup: true, faces: 1, faceSpan: 0.36 },
          { index: 2, url: URLS[2], skipped: false, closeup: false, faces: 1, faceSpan: 0.08 },
        ],
        summary: { total: 3, closeupCount: 1, recommendIndex: 2 },
      },
    });

    renderPage();
    // 从「历史作品选取」里把带 3 张候选的素材加进画布 → 节点里出现候选切换器
    const thumb = await screen.findByAltText('陈浔闻焦味');
    fireEvent.click(thumb.closest('button')!);

    await waitFor(() => expect(screen.getByText('面部特写')).toBeInTheDocument());
    expect(screen.getByText(/1 张疑似面部特写/)).toBeInTheDocument();

    // 接线没断：真的按候选 URL 打了质检接口（只带图，不带别的）
    const call = spy.mock.calls.find((c) => String(c[0]).includes('/v1/qc/images'))!;
    const body = JSON.parse(String((call[1] as RequestInit).body));
    expect(body.urls).toEqual(URLS);
  });

  it('全是干净候选时不打标（避免每张图都挂个徽标）', async () => {
    stubQc({
      code: 0,
      data: {
        results: URLS.map((u, index) => ({
          index, url: u, skipped: false, closeup: false, faces: 1, faceSpan: 0.08,
        })),
        summary: { total: 3, closeupCount: 0, recommendIndex: 0 },
      },
    });

    renderPage();
    const thumb = await screen.findByAltText('陈浔闻焦味');
    fireEvent.click(thumb.closest('button')!);

    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid^="rf__node-"]').length).toBeGreaterThan(0),
    );
    await waitFor(() => expect(screen.queryByText('面部特写')).toBeNull());
    expect(screen.queryByText(/疑似面部特写/)).toBeNull();
  });

  it('质检接口不可用时不打断画布（静默降级，无徽标）', async () => {
    const real = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
        String(input).includes('/v1/qc/images')
          ? Promise.reject(new Error('agent 没起来'))
          : real(input, init),
      ),
    );

    renderPage();
    const thumb = await screen.findByAltText('陈浔闻焦味');
    fireEvent.click(thumb.closest('button')!);

    // 画布照常工作：候选切换器还在
    await waitFor(() => expect(screen.getByText(/候选 3 张/)).toBeInTheDocument());
    expect(screen.queryByText('面部特写')).toBeNull();
  });
});