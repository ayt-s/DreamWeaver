import { useTaskEvents } from '../hooks/useTaskEvents';
import { useTaskStore } from '../store/taskStore';
import type { CreativeEvent } from '../types/task';

const NODE_NAMES: Record<string, string> = {
  requirement_parser: '需求解析',
  script_writer: '剧本生成',
  storyboarder: '分镜拆解',
  video_generator: '视频生成',
  qc_agent: '质量检查',
};

/**
 * Agent 轨迹时间线：实时展示每个节点的中间产物和工具调用。
 */
export default function TrajectoryPanel() {
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const { events, connected } = useTaskEvents(activeTaskId);

  return (
    <div className="trajectory-panel">
      <h3>
        创作轨迹{' '}
        {activeTaskId ? (
          <span className={connected ? 'dot-online' : 'dot-offline'}>
            {connected ? '实时连接' : '连接中断'}
          </span>
        ) : null}
      </h3>
      {!activeTaskId && <p className="hint">提交任务后，这里会实时显示 Agent 的创作过程。</p>}
      <ul>
        {events.map((ev) => (
          <TrajectoryItem key={ev.eventId} event={ev} />
        ))}
      </ul>
    </div>
  );
}

function TrajectoryItem({ event }: { event: CreativeEvent }) {
  switch (event.type) {
    case 'node_entered':
      return (
        <li>
          <strong>▶ {NODE_NAMES[event.data.nodeId ?? ''] ?? event.data.nodeName ?? event.data.nodeId}</strong>
        </li>
      );
    case 'node_completed':
      return <li>✓ {NODE_NAMES[event.data.nodeId ?? ''] ?? event.data.nodeId} 完成</li>;
    case 'tool_called':
      return <li>🔧 调用工具：{event.data.toolName}</li>;
    case 'progress':
      return <li>⏳ 生成中… {event.data.progress != null ? `${event.data.progress}%` : ''}</li>;
    case 'completed':
      return <li>✅ 视频生成完成</li>;
    case 'failed':
      return <li className="error">❌ 失败：{event.data.error}</li>;
    default:
      return null;
  }
}