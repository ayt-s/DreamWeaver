import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 供 vitest 使用：与环境无关的纯组件测试
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
});