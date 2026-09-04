import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import {
  ArrowLeft,
  Film,
  Loader2,
  AlertCircle,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { useState } from 'react';
import { listTasks } from '../api/tasks';
import TaskCard from '../components/TaskCard';
import { GEN_TYPE_FILTERS, type GenType } from '../types/task';

/** 每页条数：卡片较高，画廊用 6 比较合适 */
const PAGE_SIZE = 6;

/**
 * 画廊页：历史生成任务的分页列表 + 生成类型筛选。
 * 分类选项来自 GEN_TYPE_FILTERS（文生图/文生视频/图生视频，待补充类型直接加数组即可）。
 */
export default function GalleryPage() {
  const [page, setPage] = useState(1);
  const [genType, setGenType] = useState<GenType | ''>('');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['tasks', page, genType],
    queryFn: () => listTasks({ page, size: PAGE_SIZE, genType }),
    // 有排队/进行中的任务时每 5s 轮询刷新；全为终态则停止轮询
    refetchInterval: (query) => {
      const list = query.state.data?.list;
      const hasActive = list?.some(
        (t) => !['completed', 'failed', 'expired'].includes(t.status),
      );
      return hasActive ? 5000 : false;
    },
  });

  const tasks = data?.list ?? [];
  const total = data?.total ?? 0;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  const switchFilter = (key: GenType | '') => {
    setGenType(key);
    setPage(1);
  };

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      {/* Header */}
      <div className="mb-6 flex items-center gap-3">
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
          <p className="text-xs text-slate-500">历史生成任务与成片</p>
        </div>
      </div>

      {/* 生成类型筛选 */}
      <div className="mb-5 flex flex-wrap items-center gap-2">
        {GEN_TYPE_FILTERS.map((f) => {
          const active = f.key === genType;
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => switchFilter(f.key)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
                active
                  ? 'bg-violet-600 text-white shadow-sm'
                  : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
              }`}
            >
              {f.label}
            </button>
          );
        })}
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
      {!isLoading && !isError && tasks.length === 0 && (
        <div className="flex flex-col items-center py-20 text-center">
          <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100">
            <Film className="h-8 w-8 text-slate-400" />
          </div>
          <p className="text-sm font-medium text-slate-700">
            {genType === '' ? '还没有历史作品' : '该分类下暂无作品'}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {genType === ''
              ? '去创作页提交一条需求，成果会出现在这里'
              : '换个分类看看，或去创作页生成一条'}
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
      {!isLoading && !isError && tasks.length > 0 && (
        <motion.div layout className="space-y-5">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} />
          ))}
        </motion.div>
      )}

      {/* Pagination */}
      {!isLoading && !isError && total > 0 && (
        <div className="mt-8 flex items-center justify-between border-t border-slate-100 pt-4">
          <p className="text-xs text-slate-400">
            共 {total} 条 · 第 {page} / {totalPages} 页
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              上一页
            </button>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              下一页
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </main>
  );
}