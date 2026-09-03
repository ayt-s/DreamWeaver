import { useEffect, useRef, useState } from 'react';
import type { CreativeEvent } from '../types/task';

/**
 * SSE 轨迹订阅 hook。
 * 事件格式：data: {type, nodeId, progress, ...}\n\n
 * 断线自动重连（EventSource 原生），组件卸载自动关闭。
 *
 * Phase 1：后端 SSE 端口未就绪时，此 hook 仅保连接不报错；
 * Phase 2：对接 FastAPI /v1/tasks/{id}/events 或 Java 转发的 SSE。
 */
export function useTaskEvents(taskId: number | null) {
  const [events, setEvents] = useState<CreativeEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (taskId == null) return;

    const es = new EventSource(`/api/tasks/${taskId}/events`);
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    es.onmessage = (msg: MessageEvent<string>) => {
      try {
        const event = JSON.parse(msg.data) as CreativeEvent;
        setEvents((prev) => [...prev, event]);
      } catch {
        // 忽略无法解析的消息
      }
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [taskId]);

  return { events, connected, clear: () => setEvents([]) };
}