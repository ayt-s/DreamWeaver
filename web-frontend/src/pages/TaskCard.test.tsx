import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import TaskCard from '../components/TaskCard';
import CreatePanel from '../components/CreatePanel';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { TaskResponse } from '../types/task';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

const baseTask: TaskResponse = {
  id: 1,
  sessionId: 'sess-1',
  status: 'completed',
};

// TaskCard 现在依赖 useQueryClient，需要 Provider 包裹
const wrapTaskCard = (task: TaskResponse) => (
  <QueryClientProvider client={queryClient}>
    <MemoryRouter>
      <TaskCard task={task} />
    </MemoryRouter>
  </QueryClientProvider>
);

describe('TaskCard 生成类型展示', () => {
  it('novel_image 任务渲染图片卡片而非视频', () => {
    const { container } = render(
      wrapTaskCard({
        ...baseTask,
        genType: 'novel_image',
        imageUrls: JSON.stringify([
          'http://mock/image/ch1.png',
          'http://mock/image/ch2.png',
        ]),
      }),
    );
    expect(screen.getByText('小说转图')).toBeInTheDocument();
    const imgs = screen.getAllByRole('img');
    expect(imgs).toHaveLength(2);
    expect((imgs[0] as HTMLImageElement).src).toContain('ch1.png');
    expect(container.querySelector('video')).toBeNull();
  });

  it('text_video 任务渲染视频卡片', () => {
    const { container } = render(
      wrapTaskCard({
        ...baseTask,
        genType: 'text_video',
        resultJson: JSON.stringify(['http://mock/minio/a.mp4']),
      }),
    );
    expect(screen.getByText('文生视频')).toBeInTheDocument();
    expect(container.querySelectorAll('video')).toHaveLength(1);
  });
});

describe('TaskCard 画廊管理操作', () => {
  it('终态任务展示「重新生成」「删除」操作', () => {
    render(wrapTaskCard({ ...baseTask, status: 'failed' }));
    expect(screen.getByText('重新生成')).toBeInTheDocument();
    expect(screen.getByText('删除')).toBeInTheDocument();
  });

  it('进行中任务展示「删除」但无「重新生成」', () => {
    render(wrapTaskCard({ ...baseTask, status: 'queued' }));
    expect(screen.queryByText('重新生成')).not.toBeInTheDocument();
    expect(screen.getByText('删除')).toBeInTheDocument();
  });

  it('删除需二次点击确认，点击操作不直接发请求（无 Provider 层错误）', () => {
    render(wrapTaskCard({ ...baseTask, status: 'failed' }));
    fireEvent.click(screen.getByText('删除'));
    expect(screen.getByText('再次点击确认')).toBeInTheDocument();
  });
});

describe('CreatePanel 生成类型入口', () => {
  it('渲染三种生成类型选项', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CreatePanel />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText('文生视频')).toBeInTheDocument();
    expect(screen.getByText('图生视频')).toBeInTheDocument();
    expect(screen.getByText('小说转图')).toBeInTheDocument();
  });

  it('切换到小说转图后 placeholder 变化', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CreatePanel />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByPlaceholderText(/描述你想创作的视频内容/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('小说转图'));
    expect(screen.getByPlaceholderText(/粘贴小说章节/)).toBeInTheDocument();
  });
});