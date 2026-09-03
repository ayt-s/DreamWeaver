import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CreatePage from '../pages/CreatePage';

// CreatePage 依赖 TanStack Query（CreatePanel 用 useMutation），测试需 Provider 包裹
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

describe('CreatePage', () => {
  it('渲染标题与创作工作台', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <CreatePage />
      </QueryClientProvider>,
    );
    expect(screen.getByText('DreamWeaver — AI 短视频创作')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/一句话描述你想创作的视频/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始创作' })).toBeInTheDocument();
  });
});