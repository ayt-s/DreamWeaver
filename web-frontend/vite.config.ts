import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发期代理：前端 5173 → Java 8080
// 测试环境（vitest）由 vitest.config 覆盖
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
