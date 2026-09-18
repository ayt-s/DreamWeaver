/**
 * 展示层破图/坏视频兜底（agnes 产物 URL 保留期**官方零承诺**，2026-09-18 调研）。
 *
 * 这是「体验护栏」而不是功能测试，锁三条语义：
 * 1. 加载失败**不能静默**：此前 onError 只是把 <img> 隐掉，用户看到的是一个空灰框，
 *    既不知道出了什么事、也不知道该点哪里。
 * 2. 文案必须指向**真正的下一跳**：
 *    - agnes CDN 产物（图片 / 分段视频）→「产物可能已被清理，可重新生成 / 按段重生」；
 *    - **成片是 agent 本地 ffmpeg 产物（/v1/files/**）**，不能照抄「产物已过期」——
 *      那会把用户指去重新生成（真该点的是「重新拼接」）。指错组件的文案比没有文案更糟。
 * 3. 一律不写「网络异常」：本项目已有一条教训（别把归属不明的失败写成网络问题）。
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactElement } from 'react';

import TaskCard from './TaskCard';
import TrajectoryPanel from './TrajectoryPanel';
import SegmentManager from './SegmentManager';
import SlideshowPanel from './SlideshowPanel';
import { getTask } from '../api/tasks';
import { useTaskStore } from '../store/taskStore';
import type { TaskResponse } from '../types/task';

// TrajectoryPanel 自己拉任务详情 + 订阅 SSE；这里只喂一份「已完成的视频任务」，不连真接口
vi.mock('../api/tasks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tasks')>();
  return { ...actual, getTask: vi.fn() };
});
vi.mock('../hooks/useTaskEvents', () => ({
  useTaskEvents: () => ({ events: [], connected: true }),
}));

function wrap(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

const baseTask: TaskResponse = { id: 7, sessionId: 'sess-7', status: 'completed' };

describe('TaskCard：图片产物破图不再是空框', () => {
  it('图片加载失败 → 明确占位 + 指回「重新生成」', () => {
    render(
      wrap(
        <TaskCard
          task={{
            ...baseTask,
            genType: 'text_image',
            imageUrls: JSON.stringify(['https://cdn.agnes-ai.space/a.png']),
          }}
        />,
      ),
    );

    fireEvent.error(screen.getByRole('img'));

    // 破图要从页面上消失（换成占位），不能留一个点了没反应的空框
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByText('图片加载失败')).toBeInTheDocument();
    expect(screen.getByText(/可点下方「重新生成」重新出图/)).toBeInTheDocument();
    // 归属必须写清是「产物被清理」，不是「网络异常」
    expect(screen.getByText(/产物可能已被清理/)).toBeInTheDocument();
    expect(screen.queryByText(/网络/)).toBeNull();
  });
});

describe('TaskCard：视频产物坏链兜底', () => {
  it('标准模式分段视频加载失败 → 「视频加载失败」+ 指回「按段重生」', () => {
    const { container } = render(
      wrap(
        <TaskCard
          task={{
            ...baseTask,
            genType: 'text_video',
            resultJson: JSON.stringify(['https://cdn.agnes-ai.space/s1.mp4', 'https://cdn.agnes-ai.space/s2.mp4']),
          }}
        />,
      ),
    );
    expect(container.querySelectorAll('video')).toHaveLength(2);

    fireEvent.error(container.querySelectorAll('video')[0]);

    expect(container.querySelectorAll('video')).toHaveLength(1);
    expect(screen.getByText('视频加载失败')).toBeInTheDocument();
    expect(screen.getByText(/可点「按段重生」重新生成这一段/)).toBeInTheDocument();
  });

  it('★ 成片加载失败不能照抄「产物已过期」：成片是本地拼接产物，该点的是「重新拼接」', () => {
    const { container } = render(
      wrap(
        <TaskCard
          task={{
            ...baseTask,
            genType: 'text_video',
            resultJson: JSON.stringify([
              '/v1/files/final.mp4',
              'https://cdn.agnes-ai.space/s1.mp4',
              'https://cdn.agnes-ai.space/s2.mp4',
            ]),
          }}
        />,
      ),
    );

    // 画布模式：第 1 个 <video> 是成片，其后是分段缩略图
    const finalVideo = container.querySelectorAll('video')[0];
    expect(finalVideo).not.toBeNull();
    fireEvent.error(finalVideo);

    expect(screen.getByText('成片加载失败')).toBeInTheDocument();
    expect(screen.getByText(/可点下方「重新拼接」用现有分段重跑一次/)).toBeInTheDocument();
    // 成片不是 agnes 链接 → 不能说「产物可能已被清理」（会把人指去重新生成）
    expect(screen.queryByText(/产物可能已被清理/)).toBeNull();
  });

  it('画布模式的分段缩略视频失效 → 缩略格上给「加载失败」，不是黑框', () => {
    const { container } = render(
      wrap(
        <TaskCard
          task={{
            ...baseTask,
            genType: 'text_video',
            resultJson: JSON.stringify([
              '/v1/files/final.mp4',
              'https://cdn.agnes-ai.space/s1.mp4',
              'https://cdn.agnes-ai.space/s2.mp4',
            ]),
          }}
        />,
      ),
    );

    const thumbs = Array.from(container.querySelectorAll('video')).slice(1);
    expect(thumbs).toHaveLength(2);
    fireEvent.error(thumbs[0]);

    expect(screen.getByText('加载失败')).toBeInTheDocument();
    // 段号标签仍在（占位不能把「第 N 段」这一行挤掉）
    expect(screen.getByText('第 1 段')).toBeInTheDocument();
  });
});

describe('TrajectoryPanel：创作轨迹里的结果视频坏链兜底', () => {
  it('视频加载失败 → 换成可操作提示，不留黑框', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...baseTask,
      id: 9,
      resultJson: JSON.stringify(['https://cdn.agnes-ai.space/out.mp4']),
    });
    useTaskStore.setState({ activeTaskId: 9 });

    const { container } = render(wrap(<TrajectoryPanel />));
    await screen.findByText('生成结果');

    fireEvent.error(container.querySelector('video')!);

    expect(container.querySelector('video')).toBeNull();
    expect(screen.getByText('视频加载失败')).toBeInTheDocument();
    expect(screen.getByText(/可在画廊里重新生成该任务/)).toBeInTheDocument();
  });
});

describe('按段重生面板：段缩略图失效要说清（原先只是把图隐掉）', () => {
  it('SegmentManager：缩略图失效 → 「图失效」占位 + 列表上方一句可操作提示', () => {
    render(
      <SegmentManager
        taskId={1}
        genType="text_image"
        onClose={() => {}}
        onChanged={() => {}}
        segments={[
          { index: 0, prompt: '第一张', thumbnail: 'https://cdn.agnes-ai.space/a.png' },
          { index: 1, prompt: '第二张', thumbnail: 'https://cdn.agnes-ai.space/b.png' },
        ]}
      />,
    );

    expect(screen.getAllByRole('img')).toHaveLength(2);
    fireEvent.error(screen.getAllByRole('img')[0]);

    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getByText('图失效')).toBeInTheDocument();
    expect(screen.getByText(/勾选这些段重生即可重新出图/)).toBeInTheDocument();
  });

  it('SlideshowPanel：原图失效 → 「图失效」占位，并提示别勾选、要回画廊重新生成', () => {
    render(
      <SlideshowPanel
        task={{
          ...baseTask,
          genType: 'text_image',
          imageUrls: JSON.stringify(['https://cdn.agnes-ai.space/a.png', 'https://cdn.agnes-ai.space/b.png']),
        }}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    fireEvent.error(screen.getAllByRole('img')[0]);

    expect(screen.getByText('图失效')).toBeInTheDocument();
    expect(screen.getByText(/有 1 张图加载失败/)).toBeInTheDocument();
    expect(screen.getByText(/请先回画廊重新生成该任务/)).toBeInTheDocument();
  });
});
