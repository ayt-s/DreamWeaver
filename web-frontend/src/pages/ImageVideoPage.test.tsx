import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, describe, expect, it } from 'vitest';
import ImageVideoPage from './ImageVideoPage';

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
    // 初始画布节点（文本节点 1 已在画布内）
    expect(screen.getAllByText('自己编写').length).toBeGreaterThanOrEqual(1);
    // 比例预设 + 底部模型选择 + 提交按钮
    expect(screen.getAllByText('16:9').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('视频模型')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成成片' })).toBeInTheDocument();
  });
});