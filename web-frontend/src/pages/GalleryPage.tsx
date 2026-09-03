import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { ArrowLeft, Film, Loader2, AlertCircle } from 'lucide-react';
import { listTasks } from '../api/tasks';
import TaskCard from '../components/TaskCard';

/**
 * 画廊页：展示历史生成任务与成品视频。
 */
export default function GalleryPage() {
  const { data: tasks, isLoading, isError, error } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => listTasks(20),
  });

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      {/* Header */}
      <div className="mb-8 flex items-center gap-3">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100"
        >
          <ArrowLeft className="h-4 w-4" />
          返回创作
        </Link>
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-purple-600">
          <Film className="h-5 w-5 text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-slate-900">作品画廊</h1>
          <p className="text-xs text-slate-500">最近生成的历史任务与成片</p>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-sm text-slate-500">
          <Loader2 className="mr-2 h-5 w-5 animate-spin text-violet-500" />
          加载历史任务…
        </div>
      )}

      {/* Error */}
      {isError && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center py-20 text-center"
        >
          <AlertCircle className="h-10 w-10 text-red-500" />
          <p className="mt-3 text-sm font-medium text-slate-700">加载失败</p>
          <p className="mt-1 text-xs text-slate-500">
            {error instanceof Error ? error.message : '请稍后重试'}
          </p>
        </motion.div>
      )}

      {/* Empty state */}
      {!isLoading && !isError && (!tasks || tasks.length === 0) && (
        <div className="flex flex-col items-center py-20 text-center">
          <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100">
            <Film className="h-8 w-8 text-slate-400" />
          </div>
          <p className="text-sm font-medium text-slate-700">还没有历史作品</p>
          <p className="mt-1 text-xs text-slate-500">
            去创作页提交一条需求，成果会出现在这里
          </p>
          <Link
            to="/"
            className="mt-4 rounded-lg bg-violet-600 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-violet-700"
          >
            去创作
          </Link>
        </div>
      )}

      {/* Task grid */}
      {!isLoading && !isError && tasks && tasks.length > 0 && (
        <motion.div layout className="space-y-5">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} />
          ))}
        </motion.div>
      )}
    </main>
  );
}