import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CreatePage from './pages/CreatePage';
import GalleryPage from './pages/GalleryPage';
import ImageVideoPage from './pages/ImageVideoPage';
import NovelPage from './pages/NovelPage';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<CreatePage />} />
          <Route path="/gallery" element={<GalleryPage />} />
          <Route path="/canvas" element={<ImageVideoPage />} />
          <Route path="/novel" element={<NovelPage />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);