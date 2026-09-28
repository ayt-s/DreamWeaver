import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// jsdom 没有实现 Element.scrollIntoView，而组件在「滚动到底部」时会调用它
// （如 ChatPanel 的 bottomRef）—— 不 stub 的话渲染阶段就抛
// TypeError: bottomRef.current?.scrollIntoView is not a function
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

// ★ 2026-09-24：Node ≥ 22.4 自带实验性 Web Storage（`globalThis.localStorage` /
// `sessionStorage`）。Node v26 起它**默认存在**，而没给 `--localstorage-file` 时
// 取值就是 `undefined`；vitest 的 jsdom 环境只补「尚不存在的全局」，
// 于是应用代码里的 `localStorage.getItem(...)` 命中的是 Node 那份 undefined：
//   TypeError: Cannot read properties of undefined (reading 'getItem')
// （实测 Node v26.7.0：8 个文件 47 例挂在这里，且与业务改动无关 ——
//  同一份代码在旧 Node 上是绿的。）
// jsdom 的 window 上有真实现，这里显式接回全局；拿不到就退化成内存实现，
// 至少让「读不到 = null」而不是把整个渲染打挂。
function installWebStorageShim(): void {
  if (typeof window === 'undefined') return;
  const memory = new Map<string, string>();
  const fallback: Storage = {
    get length() {
      return memory.size;
    },
    clear: () => memory.clear(),
    getItem: (k: string) => (memory.has(k) ? memory.get(k)! : null),
    key: (i: number) => Array.from(memory.keys())[i] ?? null,
    removeItem: (k: string) => void memory.delete(k),
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
  };
  (['localStorage', 'sessionStorage'] as const).forEach((key) => {
    let impl: Storage | undefined;
    try {
      impl = window[key];
    } catch {
      impl = undefined; // 不透明 origin 下 jsdom 会抛，而不是返回 undefined
    }
    Object.defineProperty(globalThis, key, {
      value: impl ?? fallback,
      configurable: true,
      writable: true,
    });
  });
}

installWebStorageShim();

// 每个测试后清理 DOM，避免跨用例污染
afterEach(() => {
  cleanup();
});
