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
 * ## 第 5 条也被否（2026-09-20 追加，务必先读这条再动手）
 * 5) **门闸 + 出口清理同时在位**：既拦入口（URL 载荷只对带入项目生效），又在切项目时
 *    **按认领时记下的键名把外来键从 `anchorCharRefs/anchorSceneRefs` state 里剥掉** ——
 *    **仍然红**。
 *    ⇒ 结论：**外来图根本不经过「anchors state → URL 兜底 → 合并 effect」这条链**。
 *    下一步必须先打点，别再改代码。最省的探针（我两次都做失败了，提示：探针文件要
 *    完全照抄下面那个可用的测试文件的 mock 写法，别自己简化）：
 *      a) 在第二次点击「文生图」时把 `anchors`（= `useContext(AnchorsCtx)`，见 ~396 行）
 *         与 `refs`（= `firstFrameRefsFor(...)`，见 ~568 行）打出来；
 *      b) 若 `anchors` 干净而载荷仍带外来图 ⇒ 嫌疑落到 `refImagesField(refs)` 或
 *         `urlMapOf()` 的缓存；
 *      c) 若 `anchors` 不干净 ⇒ 顺着 `anchorsCtxValue`（~1239）往上找是谁在给它喂数据。
 *
 * ## 已试过、**全部被否**的三条（2026-09-20，逐条实测）
 * 1. `4276dd0`：切项目时把 `?anchorRefs` 从 URL 清掉 —— 不够；
 * 2. 再给「URL 载荷合并进 state」的 effect 加 owner 门闸 —— **仍然红**；
 * 3. 再给 `effectiveCharRefs / effectiveSceneRefs` 的**兜底**加 owner 门闸
 *    （URL 载荷不属于当前项目就不许兜底）—— **仍然红**。
 * ⇒ 外来锚定图是通过**第四条路**活下来的，我还没定位到。**停手不改**（不留未验证的行为变更）。
 *
 * ## 下一条线索（按嫌疑排序）
 * a) `锚定图来源`（`anchors = useContext(AnchorsCtx)`，节点组件在 ~396 行）—— 若 image 节点组件是
 *    `React.memo` 且比较器忽略了 context，切换项目后**节点不会重渲染** ⇒ handler 拿到旧 anchors；
 * b) 「切换项目时从项目数据同步锚定图 state」那条 effect：`parseAnchorRefs(p.characterRefs)` 在
 *    项目没有锚定图（`characterRefs` 为 undefined）时**是否真的返回空对象** —— 若它抛错/返回非空，
 *    state 里的外来图就永远清不掉；
 * c) `plan`/提交那条路径是否还有**第二处**读 `anchorRefs`（URL）的地方没被门闸覆盖。
 * 查法：在用例里分别对 a/b/c 打点（例如断言切换后节点组件是否重渲染、`anchorCharRefs` 是否为空），
 * 先把"外来图到底从哪个变量流进载荷"这条链子钉死，再改代码。
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

  it('切到另一个项目后，外来的锚定图不再出现在提交载荷里', async () => {
    const { container } = renderAt(
      `/canvas?anchorRefs=${encodeURIComponent(JSON.stringify(FOREIGN))}`,
    );
    await screen.findByTitle(/切换画布项目/);
    // ★★ 关键前置条件（前五条修法全被否就是栽在这里）：
    //   「带入时那个项目」必须**先真的被选中** —— 页面不会自动选中第一个项目
    //   （currentProjectId 只在用户下拉选择/新建保存后才设置）。
    //   不先选项目 1，载荷就是"没有项目时"带进来的，随后切到项目 2 时认领 2 是**正确行为**，
    //   "跨项目污染"这个前提根本没建立 ⇒ 用例会永远红，而产品代码是对的。
    fireEvent.change(container.querySelector('select') as HTMLSelectElement, { target: { value: '1' } });
    await waitFor(() =>
      expect([...container.querySelectorAll('input')].some((i) => i.value === '长生烬9-17-1')).toBe(true));

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
