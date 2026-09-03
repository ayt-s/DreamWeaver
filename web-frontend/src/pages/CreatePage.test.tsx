import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import CreatePage from '../pages/CreatePage';

// CreatePage 依赖 TanStack Query + React Router（子代理加了画廊链接），测试需双 Provider 包裹
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe('CreatePage', () => {
  it('渲染标题与创作工作台', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CreatePage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getAllByText(/AI 导演 Agent/).length).toBeGreaterThan(0);
    expect(screen.getByPlaceholderText(/描述你想创作的视频/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始创作' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /历史作品/ })).toBeInTheDocument();
  });
});