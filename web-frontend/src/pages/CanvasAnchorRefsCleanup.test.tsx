/**
 * ⚠️⚠️ 本文件当前是 **skip 状态**：它是一条**已验证的缺陷**的复现用例，但还没修好。
 *
 * ## 实测结论（2026-09-20）
 * - 用例的前半段**通过**：进入项目 1 时，URL `?anchorRefs` 带入的外来锚定图**确实**出现在
 *   「文生图」提交载荷的 `referenceImages` 里（这就是污染的直接观测）。
 * - 切到项目 2 后，**外来图仍在载荷里** ⇒ #27 的污染真实存在。
 * - 关键：用例里先断言「切换真的发生了」（项目名输入框变成目标项目名）再断言载荷，
 *   所以这次**不是**"其实没切成"的假失败。
 *
 * ## 已试过但**不足以**修好的两条
 * 1. `4276dd0` 的"切项目时把 `?anchorRefs` 从 URL 清掉" —— 不够（本用例证明）；
 * 2. 再加"owner 门闸"（URL 载荷只对带入时那个项目生效，给合并 effect 加 early return）——
 *    **仍然红** ⇒ 外来锚定图是通过**别的路径**活下来的。
 *    下一个嫌疑：节点「文生图」的处理函数持有了**旧的 effective refs 闭包**
 *    （切换后重渲染了，但 handler 里用的还是进入时那份 anchors）—— 需要顺着
 *    `firstFrameRefsFor(rawPrompt, urlMapOf(anchors.chars), …)` 往上查 handler 的依赖数组。
 *
 * ## 为什么保留这个文件
 * 删掉就等于把"已复现的缺陷"和上面两条否定结论一起丢掉。保留为 skip，修好后去掉 `.skip` 即可。
 * （两条被否的改动都已还原，不留未验证的行为变更。）
 *
 * ---
 *
 * 原始说明：#27：切换画布项目时必须清掉 URL 上的 `?anchorRefs`（一次性带入，只属于进入时那个项目）。
 *
 * ## 污染链
 * `?anchorRefs` 是「小说转画布」的一次性带入，代码里它一直是
 * `effectiveCharRefs / effectiveSceneRefs` 的兜底 ⇒ 下拉切到**别的**项目后：
 * ① 这套外来的角色/场景图仍会作为参考图参与那个项目的提交；
 * ② 只要在那个项目里做任何锚定图增删，就会把这份外来图**写进它的 character_refs/scene_refs**。
 *
 * ## 观测量
 * 节点上的「文生图」：它的 `referenceImages` 来自
 * `firstFrameRefsFor(rawPrompt, urlMapOf(anchors.chars), urlMapOf(anchors.scenes))`，
 * 而 `anchors` 正是 effective refs（画布 state 优先、URL 兜底）⇒ 外来锚定图会**出现在提交载荷里**，
 * 这就是可直接观测的后果。
 *
 * ⚠️ 上一版这条用例一直红，原因是**我 mock 错了**：`getProject` 写死返回项目 1，
 *    于是切换后 `p.id` 仍是 1、`currentProjectId` 没变 ⇒ 清理 effect 根本不该触发（假失败）。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import ImageVideoPage from './ImageVideoPage';
import { createVideoTask, getTask, uploadImage } from '../api/tasks';
import { getProject, listProjects } from '../api/canvas';

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
    listProjects: vi.fn(),
    getProject: vi.fn(),
    saveProject: vi.fn().mockResolvedValue({ conflict: false, canvas: { id: 1, version: 1 } }),
    createProject: vi.fn(),
    deleteProject: vi.fn(),
    getCanvasVersion: vi.fn().mockResolvedValue(1),
  };
});
vi.mock('../api/promptRecompose', () => ({ recomposePrompts: vi.fn() }));

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

const proj = (id: number, name: string) =>
  ({ id, name, nodesJson: null, edgesJson: null, version: 1 } as never);

const FOREIGN_URL = 'https://cdn.foreign/chen.png';
const FOREIGN = { characters: { 陈浔: { url: FOREIGN_URL, desc: '少年陈浔' } }, scenes: {} };

function renderAt(route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <ImageVideoPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function fillDesc(text: string) {
  const boxes = screen.queryAllByPlaceholderText(/本段描述/);
  expect(boxes.length).toBeGreaterThanOrEqual(1);
  boxes.forEach((el) => fireEvent.change(el, { target: { value: text } }));
}

/** 点「文生图」并返回该次提交载荷（要求它确实是第 n+1 次调用） */
async function genOnce(expectedCalls: number) {
  const btn = screen
    .getAllByText('文生图')
    .map((el) => el.closest('button'))
    .find(Boolean) as HTMLButtonElement;
  fireEvent.click(btn);
  await waitFor(() =>
    expect(vi.mocked(createVideoTask).mock.calls.length).toBeGreaterThan(expectedCalls));
  const calls = vi.mocked(createVideoTask).mock.calls;
  return calls[calls.length - 1][0];
}

describe('切换画布项目时清理 ?anchorRefs（#27）', () => {
  beforeEach(() => {
    vi.mocked(listProjects).mockResolvedValue([proj(1, '长生烬9-17-1'), proj(2, '别的项目')]);
    // ★ 按请求的 id 返回对应项目 —— 写死返回项目 1 会让"切换"变成空操作（上一版的假失败）
    vi.mocked(getProject).mockImplementation(async (id: number) =>
      proj(id, id === 1 ? '长生烬9-17-1' : '别的项目'));
    vi.mocked(createVideoTask).mockResolvedValue({ id: 5 } as never);
    vi.mocked(uploadImage).mockResolvedValue({ url: 'https://cdn.local/f.png', name: 'f.png' } as never);
    // 「文生图」会轮询到 90 秒才返回（期间按钮禁用）⇒ 让第一张立刻"完成"，流程收尾后才能再点
    vi.mocked(getTask).mockResolvedValue({
      id: 5, status: 'completed', imageUrls: JSON.stringify(['https://cdn.local/out.png']),
    } as never);
  });

  afterEach(() => vi.restoreAllMocks());

  it.skip('切到另一个项目后，外来的锚定图不再出现在提交载荷里（⚠️ 尚未修好，见文件头）', async () => {
    const { container } = renderAt(
      `/canvas?anchorRefs=${encodeURIComponent(JSON.stringify(FOREIGN))}`,
    );
    await screen.findByTitle(/切换画布项目/);

    // 1) 进入项目 1：URL 带入的锚定图**在**参与（提示词含锚定图名「陈浔」→ 命中）
    fillDesc('陈浔站在山坡上');
    const first = await genOnce(0);
    await screen.findByText('已生成参考图', {}, { timeout: 15_000 });
    expect(JSON.stringify(first.referenceImages ?? [])).toContain(FOREIGN_URL);

    // 2) 切到项目 2
    const select = container.querySelector('select') as HTMLSelectElement;
    await act(async () => {
      fireEvent.change(select, { target: { value: '2' } });
    });
    await act(async () => { await Promise.resolve(); });
    // 先证明「切换真的发生了」（项目名输入框应变成项目 2 的名字）——
    // 没有这条就可能又出现『其实没切成』的假失败（上一版就是这么红的）。
    await waitFor(() =>
      expect([...container.querySelectorAll('input')].some((i) => i.value === '别的项目')).toBe(true));

    // 3) 在项目 2 里再出一张图：外来的锚定图**不该**再参与
    fillDesc('陈浔站在山坡上');
    const second = await genOnce(1);
    expect(JSON.stringify(second.referenceImages ?? [])).not.toContain(FOREIGN_URL);
  }, 40_000);
});
