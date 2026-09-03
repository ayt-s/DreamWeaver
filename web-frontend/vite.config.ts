import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发期代理：前端 5173 → Java 8080
// Java 业务侧再转发 FastAPI（模型侧）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
});