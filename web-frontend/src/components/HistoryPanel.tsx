import { motion } from 'framer-motion';
import { Video, Trash2, Calendar } from 'lucide-react';
import type { TaskResponse } from '../types/task';
import { parseResultUrls } from '../types/task';
import { useTaskStore } from '../store/taskStore';

interface HistoryPanelProps {
  tasks: TaskResponse[];
}

export default function HistoryPanel({ tasks }: HistoryPanelProps) {
  const clearCompleted = useTaskStore((s) => s.clearActiveTask);
  const removeTask = useTaskStore((s) => {
    // TODO: add removeCompletedTask to store
    return () => {};
  });

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="rounded-2xl border border-slate-200 bg-white p-6 shadow-lg"
    >
      <div className="mb-4 flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100">
          <Video className="h-4 w-4 text-slate-600" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-900">创作历史</h3>
          <p className="text-xs text-slate-500">{tasks.length} 个已完成任务</p>
        </div>
      </div>

      <div className="space-y-3">
        {tasks.map((task, i) => {
          const urls = parseResultUrls(task.resultJson);
          const date = task.createdAt
            ? new Date(task.createdAt).toLocaleString('zh-CN')
            : '未知时间';

          return (
            <motion.div
              key={task.id}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.05 }}
              className="flex items-center gap-4 rounded-xl border border-slate-100 bg-slate-50 p-4"
            >
              {/* Status Icon */}
              <div
                className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${
                  task.status === 'completed'
                    ? 'bg-emerald-100'
                    : task.status === 'failed'
                      ? 'bg-red-100'
                      : 'bg-amber-100'
                }`}
              >
                {task.status === 'completed' ? (
                  <Video className="h-5 w-5 text-emerald-600" />
                ) : task.status === 'failed' ? (
                  <Trash2 className="h-5 w-5 text-red-600" />
                ) : (
                  <Calendar className="h-5 w-5 text-amber-600" />
                )}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-700">
                    任务 #{task.id}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs ${
                      task.status === 'completed'
                        ? 'bg-emerald-100 text-emerald-700'
                        : task.status === 'failed'
                          ? 'bg-red-100 text-red-700'
                          : 'bg-amber-100 text-amber-700'
                    }`}
                  >
                    {task.status.replace(/_/g, ' ')}
                  </span>
                </div>
                <p className="mt-1 text-xs text-slate-500">{date}</p>
                {urls.length > 0 && (
                  <p className="mt-1 text-xs text-violet-600">
                    {urls.length} 个视频片段
                  </p>
                )}
              </div>

              {/* Actions */}
              <div className="flex gap-2">
                {urls.length > 0 && (
                  <a
                    href={urls[0]}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 transition-colors"
                  >
                    查看
                  </a>
                )}
              </div>
            </motion.div>
          );
        })}
      </div>
    </motion.div>
  );
}
