import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ImageVideoPage from './ImageVideoPage';

// 画布独立页冒烟：确保页面能无运行时错误挂载（白屏回归防护）
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

describe('ImageVideoPage 画布页', () => {
  it('挂载渲染成功（标题/素材源/提交按钮齐全）', async () => {
    renderPage();
    expect(screen.getAllByText((t) => t.includes('无限画布')).length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByText((t) => t.includes('图生视频')).length,
    ).toBeGreaterThanOrEqual(1);
    // 素材来源切换 + 上传按钮
    expect(screen.getByText('历史作品')).toBeInTheDocument();
    expect(screen.getByText('本地上传')).toBeInTheDocument();
    // 提交按钮
    expect(
      screen.getByRole('button', { name: /开始创作/ }),
    ).toBeInTheDocument();
  });
});