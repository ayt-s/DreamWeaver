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

describe('TaskCard 生成类型展示', () => {
  it('novel_image 任务渲染图片卡片而非视频', () => {
    const { container } = render(
      <TaskCard
        task={{
          ...baseTask,
          genType: 'novel_image',
          imageUrls: JSON.stringify([
            'http://mock/image/ch1.png',
            'http://mock/image/ch2.png',
          ]),
        }}
      />,
    );
    expect(screen.getByText('小说转图')).toBeInTheDocument();
    const imgs = screen.getAllByRole('img');
    expect(imgs).toHaveLength(2);
    expect((imgs[0] as HTMLImageElement).src).toContain('ch1.png');
    expect(container.querySelector('video')).toBeNull();
  });

  it('text_video 任务渲染视频卡片', () => {
    const { container } = render(
      <TaskCard
        task={{
          ...baseTask,
          genType: 'text_video',
          resultJson: JSON.stringify(['http://mock/minio/a.mp4']),
        }}
      />,
    );
    expect(screen.getByText('文生视频')).toBeInTheDocument();
    expect(container.querySelectorAll('video')).toHaveLength(1);
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