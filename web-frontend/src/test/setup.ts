import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// 每个测试后清理 DOM，避免跨用例污染
afterEach(() => {
  cleanup();
});