import { motion } from 'framer-motion';
import { Video, Trash2, Hourglass } from 'lucide-react';
import type { ReactNode } from 'react';
import type { TaskResponse, TaskStatus } from '../types/task';
import { parseResultUrls } from '../types/task';

interface TaskCardProps {
  task: TaskResponse;
}

type TaskState = 'completed' | 'failed' | 'running';

function stateOf(status: TaskStatus): TaskState {
  if (status === 'completed') return 'completed';
  if (status === 'failed') return 'failed';
  return 'running';
}

const STATE_LABEL: Record<TaskState, string> = {
  completed: '完成',
  failed: '失败',
  running: '进行中',
};

const STATE_BADGE: Record<TaskState, string> = {
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  running: 'bg-amber-100 text-amber-700',
};

const STATE_ICON: Record<TaskState, ReactNode> = {
  completed: <Video className="h-4 w-4 text-emerald-600" />,
  failed: <Trash2 className="h-4 w-4 text-red-600" />,
  running: <Hourglass className="h-4 w-4 text-amber-600" />,
};

/**
 * 画廊卡片：展示单个历史生成任务及其产物视频。
 */
export default function TaskCard({ task }: TaskCardProps) {
  const state = stateOf(task.status);
  const urls = parseResultUrls(task.resultJson);

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition-shadow hover:shadow-md"
    >
      {/* Header */}
      <div className="flex items-start gap-3 p-5">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-purple-600">
          <Video className="h-5 w-5 text-white" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-900">
              任务 #{task.id}
            </span>
            <span
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STATE_BADGE[state]}`}
            >
              {STATE_ICON[state]}
              {STATE_LABEL[state]}
            </span>
          </div>
          <p className="mt-1 truncate text-xs text-slate-500">
            会话 {task.sessionId}
          </p>
          {task.errorMessage && (
            <p className="mt-2 line-clamp-2 text-xs text-red-600">
              {task.errorMessage}
            </p>
          )}
        </div>
      </div>

      {/* Videos */}
      {urls.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 border-t border-slate-100 p-5 sm:grid-cols-2">
          {urls.map((url, i) => (
            <div
              key={`${task.id}-${i}-${url}`}
              className="overflow-hidden rounded-xl border border-slate-200 bg-black"
            >
              <video
                src={url}
                controls
                preload="metadata"
                className="aspect-video w-full"
              />
            </div>
          ))}
        </div>
      ) : state === 'running' ? (
        <div className="flex items-center gap-2 border-t border-slate-100 px-5 py-4 text-xs text-slate-400">
          <Hourglass className="h-4 w-4 animate-pulse text-amber-500" />
          AI 导演正在创作中，稍后回来查看…
        </div>
      ) : (
        <div className="px-5 py-4 text-xs text-slate-400">暂无生成产物</div>
      )}
    </motion.article>
  );
}