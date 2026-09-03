import { useQuery } from '@tanstack/react-query';
import { useTaskEvents } from '../hooks/useTaskEvents';
import { getTask } from '../api/tasks';
import { useTaskStore } from '../store/taskStore';
import { parseResultUrls, type CreativeEvent } from '../types/task';

const NODE_NAMES: Record<string, string> = {
  requirement_parser: '需求解析',
  script_writer: '剧本生成',
  storyboarder: '分镜拆解',
  video_generator: '视频生成',
  qc_agent: '质量检查',
};

/**
 * Agent 轨迹时间线：SSE 实时轨迹 + 轮询兜底（完成后展示视频）。
 * Phase 1：Java 尚未提供 /events，靠 TanStack Query 轮询 getTask 兜底。
 */
export default function TrajectoryPanel() {
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const { events, connected } = useTaskEvents(activeTaskId);

  // 轮询兜底：任务激活期间每 3s 拉一次状态（Phase 2 换 SSE 后可降频/移除）
  const { data: task } = useQuery({
    queryKey: ['task', activeTaskId],
    queryFn: () => (activeTaskId != null ? getTask(activeTaskId) : null),
    refetchInterval: activeTaskId != null ? 3000 : false,
    enabled: activeTaskId != null,
  });

  const videoUrls = parseResultUrls(task?.resultJson);
  const isDone = task?.status === 'completed' || task?.status === 'failed';

  return (
    <div className="mt-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h3 className="flex items-center gap-2 text-base font-semibold">
        创作轨迹
        {task && (
          <StatusBadge status={task.status} connected={connected} />
        )}
      </h3>

      {!activeTaskId && (
        <p className="mt-2 text-sm text-slate-500">
          提交任务后，这里会实时显示 Agent 的创作过程。
        </p>
      )}

      {task && <TaskStatusLine task={task} />}

      <ul className="mt-4 flex list-none flex-col gap-2 p-0">
        {events.map((ev) => (
          <TrajectoryItem key={ev.eventId} event={ev} />
        ))}
      </ul>

      {!events.length && task && !isDone && (
        <p className="mt-2 text-sm text-slate-500">
          Agent 正在创作中…（SSE 轨迹暂未接入，Phase 2 生效）
        </p>
      )}

      {videoUrls.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-sm font-medium">生成结果</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {videoUrls.map((url, i) => (
              <video key={`${i}-${url}`} src={url} controls className="w-full rounded-lg border border-slate-200 bg-black" />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status, connected }: { status: string; connected: boolean }) {
  const color =
    status === 'completed'
      ? 'bg-emerald-100 text-emerald-700'
      : status === 'failed'
        ? 'bg-red-100 text-red-700'
        : 'bg-amber-100 text-amber-700';
  const label = connected ? status : `${status}（连接中断）`;
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>{label}</span>;
}

function TaskStatusLine({ task }: { task: { status: string; errorMessage?: string } }) {
  if (task.status === 'failed') {
    return <p className="mt-2 text-sm text-red-600">失败：{task.errorMessage ?? '未知原因'}</p>;
  }
  if (task.status === 'completed') {
    return <p className="mt-2 text-sm text-emerald-600">✅ 视频生成完成</p>;
  }
  return null;
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
      return <li className="text-red-600">❌ 失败：{event.data.error}</li>;
    default:
      return null;
  }
}