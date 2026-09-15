/**
 * useTaskEvents 的去重语义（F2 SSE 补漏）。
 *
 * **为什么必须去重**：F2 之后服务端会给每个会话保留环形缓冲，新连接不带
 * `Last-Event-ID` 时**全量重放**已有事件。于是「已在 state 里的事件」可能被
 * 重复投递 —— 典型场景是 React StrictMode 开发模式的双次挂载、以及断线重连。
 * 不去重的话轨迹面板会出现重复节点（同一 node_entered 显示两遍）。
 *
 * 锁定三条：
 * 1. 全量重放的事件按序进入
 * 2. 同一 event_id 再次投递 → 忽略
 * 3. 比当前游标更旧的 event_id → 忽略（乱序重放）
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useTaskEvents } from './useTaskEvents';

/** 最小 EventSource 替身：只实现 hook 用到的那几个成员 */
class FakeEventSource {
  static last: FakeEventSource | null = null;

  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  private listeners: Record<string, Array<(e: MessageEvent<string>) => void>> = {};

  constructor(url: string) {
    this.url = url;
    FakeEventSource.last = this;
  }

  addEventListener(type: string, cb: (e: MessageEvent<string>) => void) {
    (this.listeners[type] ||= []).push(cb);
  }

  close() {
    this.closed = true;
  }

  /** 模拟服务端推送一条**具名**事件（真实 SSE 里具名事件不会触发 onmessage） */
  emit(type: string, payload: unknown) {
    const msg = { data: JSON.stringify(payload) } as MessageEvent<string>;
    (this.listeners[type] || []).forEach((cb) => cb(msg));
  }
}

function Harness({ sessionId }: { sessionId: string | null }) {
  const { events } = useTaskEvents(sessionId);
  return (
    <div data-testid="ids">
      {events.map((e) => (e as { event_id?: number }).event_id ?? 'x').join(',')}
    </div>
  );
}

const ev = (id: number) => ({
  event_id: id,
  type: 'node_entered',
  session_id: 's1',
  timestamp: 0,
  data: { node_id: `n${id}` },
});

describe('useTaskEvents', () => {
  beforeEach(() => {
    FakeEventSource.last = null;
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('连接对应会话的事件端点', () => {
    render(<Harness sessionId="s1" />);
    expect(FakeEventSource.last?.url).toBe('/v1/tasks/s1/events');
  });

  it('全量重放的事件按序进入', () => {
    render(<Harness sessionId="s1" />);
    const es = FakeEventSource.last!;

    act(() => {
      es.emit('node_entered', ev(1));
      es.emit('progress', ev(2));
    });

    expect(screen.getByTestId('ids')).toHaveTextContent('1,2');
  });

  it('同一 event_id 重复投递 → 去重（重放场景的核心保障）', () => {
    render(<Harness sessionId="s1" />);
    const es = FakeEventSource.last!;

    act(() => {
      es.emit('node_entered', ev(1));
      es.emit('node_entered', ev(2));
    });
    expect(screen.getByTestId('ids')).toHaveTextContent('1,2');

    // 服务端重放：已经把 1、2 发过一次了，又从头发
    act(() => {
      es.emit('node_entered', ev(1));
      es.emit('node_entered', ev(2));
    });
    expect(screen.getByTestId('ids')).toHaveTextContent('1,2'); // 没有变成 1,2,1,2
  });

  it('比游标更旧的 event_id 被忽略（乱序重放）', () => {
    render(<Harness sessionId="s1" />);
    const es = FakeEventSource.last!;

    act(() => {
      es.emit('node_entered', ev(5));
      es.emit('node_entered', ev(3)); // 旧 id，应被忽略
    });

    expect(screen.getByTestId('ids')).toHaveTextContent('5');
  });

  it('没有 event_id 的事件（如反转的 replay_gap）照常追加', () => {
    render(<Harness sessionId="s1" />);
    const es = FakeEventSource.last!;

    act(() => {
      es.emit('replay_gap', { type: 'replay_gap', session_id: 's1', data: { oldest_available: 42 } });
    });

    expect(screen.getByTestId('ids')).toHaveTextContent('x');
  });

  it('卸载时关闭连接', () => {
    const { unmount } = render(<Harness sessionId="s1" />);
    const es = FakeEventSource.last!;
    expect(es.closed).toBe(false);

    unmount();
    expect(es.closed).toBe(true);
  });

  it('sessionId 为 null 时不建立连接', () => {
    render(<Harness sessionId={null} />);
    expect(FakeEventSource.last).toBeNull();
  });
});
