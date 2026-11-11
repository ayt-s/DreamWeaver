import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CreatePage from './pages/CreatePage';
import GalleryPage from './pages/GalleryPage';
import ImageVideoPage from './pages/ImageVideoPage';
import NovelPage from './pages/NovelPage';
import LoginPage from './pages/LoginPage';
import './index.css';
import NavBar from './components/NavBar';
import RequireAuth from './components/RequireAuth';

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
        <NavBar />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<RequireAuth><CreatePage /></RequireAuth>} />
          <Route path="/gallery" element={<RequireAuth><GalleryPage /></RequireAuth>} />
          <Route path="/canvas" element={<RequireAuth><ImageVideoPage /></RequireAuth>} />
          <Route path="/novel" element={<RequireAuth><NovelPage /></RequireAuth>} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);