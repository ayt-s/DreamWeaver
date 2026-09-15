import { useEffect, useRef, useState } from 'react';
import type { CreativeEvent } from '../types/task';

/**
 * SSE 轨迹订阅 hook（2026-09 接通 FastAPI /v1/tasks/{id}/events）。
 *
 * - 直连模型侧（Vite 代理 /v1 → FastAPI 8000），Java 只做业务 REST
 * - 事件格式：`id:` + `event=<type>` + `data=JSON`（见 app/events.py）
 * - 断线自动重连（EventSource 原生），组件卸载自动关闭
 * - sessionId 为 FastAPI 生成的 LangGraph 会话 ID
 *
 * **刷新页面后仍能看到轨迹**（F2）：服务端为每个会话保留环形缓冲，
 * 新连接不带 `Last-Event-ID` 时会把已缓冲的历史**全量重放**。
 * 所以整页刷新（events 状态清空）也能拿到本次任务之前的节点轨迹 ——
 * 这是服务端补漏，客户端无需额外做什么。
 *
 * AI 编码注意：重放会**重复投递**已在 state 里的事件（例如 React StrictMode
 * 开发模式的双次挂载），因此必须按 `event_id` 去重；服务端的 `event_id` 是
 * 每会话严格递增的整数，可安全用于去重与排序。
 */
export function useTaskEvents(sessionId: string | null) {
  const [events, setEvents] = useState<CreativeEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  // 已处理的最大 event_id：服务端会重放历史，不去重就会出现重复节点
  const lastIdRef = useRef<number>(0);

  useEffect(() => {
    if (!sessionId) return;

    // 新会话/重挂载 → 重新从头接收（服务端会给全量重放），去重游标一并归零
    lastIdRef.current = 0;

    const es = new EventSource(`/v1/tasks/${sessionId}/events`);
    esRef.current = es;

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    // 通用监 listening：event 类型通过 msg.type 区分（node_entered/tool_called/progress/...）
    const onEvent = (msg: MessageEvent<string>) => {
      try {
        const event = JSON.parse(msg.data) as CreativeEvent;
        // 按 event_id 去重：重放场景下同一事件可能被投递多次
        const id = (event as { event_id?: number }).event_id;
        if (typeof id === 'number') {
          if (id <= lastIdRef.current) return;
          lastIdRef.current = id;
        }
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
    // 服务端在「客户端漏掉了一段（缓冲被 ring 淘汰）」时会发这个提示事件。
    // 漏注册的后果是用户永远看不到轨迹缺口 —— 静默丢数据最难排查。
    es.addEventListener('replay_gap', onEvent);
    es.onmessage = onEvent; // 兜底：无具名 event 类型时

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [sessionId]);

  return { events, connected, clear: () => setEvents([]) };
}