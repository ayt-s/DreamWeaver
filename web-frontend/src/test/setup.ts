import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// jsdom 没有实现 Element.scrollIntoView，而组件在「滚动到底部」时会调用它
// （如 ChatPanel 的 bottomRef）—— 不 stub 的话渲染阶段就抛
// TypeError: bottomRef.current?.scrollIntoView is not a function
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// 每个测试后清理 DOM，避免跨用例污染
afterEach(() => {
  cleanup();
});
