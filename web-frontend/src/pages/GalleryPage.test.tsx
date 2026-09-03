import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import BrowseGalleryPage from '../pages/GalleryPage';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

const wrap = (ui: React.ReactNode) => (
  <QueryClientProvider client={queryClient}>
    <MemoryRouter>{ui}</MemoryRouter>
  </QueryClientProvider>
);

describe('GalleryPage', () => {
  it('标题渲染', () => {
    render(wrap(<BrowseGalleryPage />));
    expect(screen.getByText('作品画廊')).toBeInTheDocument();
  });
});