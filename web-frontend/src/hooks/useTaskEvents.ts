import { useEffect, useRef, useState } from 'react';
import type { CreativeEvent } from '../types/task';

/**
 * SSE 轨迹订阅 hook（2026-09 接通 FastAPI /v1/tasks/{id}/events）。
 *
 * - 直连模型侧（Vite 代理 /v1 → FastAPI 8000），Java 只做业务 REST
 * - 事件格式：event=<type> + data=JSON（见 app/events.py）
 * - 断线自动重连（EventSource 原生），组件卸载自动关闭
 * - sessionId 为 FastAPI 生成的 LangGraph 会话 ID
 */
export function useTaskEvents(sessionId: string | null) {
  const [events, setEvents] = useState<CreativeEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    const es = new EventSource(`/v1/tasks/${sessionId}/events`);
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    // 通用监 listening：event 类型通过 msg.type 区分（node_entered/tool_called/progress/...）
    const onEvent = (msg: MessageEvent<string>) => {
      try {
        const event = JSON.parse(msg.data) as CreativeEvent;
        setEvents((prev) => [...prev.slice(-199), event]); // 截断上限 200 条，防无限增长
      } catch {
        // 忽略无法解析的消息（含心跳注释行）
      }
    };

    es.addEventListener('node_entered', onEvent);
    es.addEventListener('node_completed', onEvent);
    es.addEventListener('tool_called', onEvent);
    es.addEventListener('tool_result', onEvent);
    es.addEventListener('interrupted', onEvent);
    es.addEventListener('progress', onEvent);
    es.addEventListener('completed', onEvent);
    es.addEventListener('failed', onEvent);
    es.onmessage = onEvent; // 兜底：无具名 event 类型时

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [sessionId]);

  return { events, connected, clear: () => setEvents([]) };
}