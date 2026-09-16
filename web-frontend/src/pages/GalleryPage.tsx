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
  CheckSquare,
  Wand2,
} from 'lucide-react';
import { useState, useCallback, useMemo } from 'react';
import { listTasks } from '../api/tasks';
import TaskCard from '../components/TaskCard';
import BatchReworkPanel from '../components/BatchReworkPanel';
import {
  GEN_TYPE_FILTERS,
  DRAFT_FILTERS,
  type GenType,
  type DraftFilter,
} from '../types/task';

/** 每页条数：卡片较高，画廊用 6 比较合适 */
const PAGE_SIZE = 6;

/**
 * 画廊页：历史生成任务的分页列表 + 生成类型筛选。
 * 分类选项来自 GEN_TYPE_FILTERS（文生图/文生视频/图生视频，待补充类型直接加数组即可）。
 *
 * 批量重生模式：点"批量重生"进入多选，选中多个有段配置的任务后
 * 点底部"段重生"按钮打开 BatchReworkPanel，逐任务勾选段提交。
 */
export default function GalleryPage() {
  const [page, setPage] = useState(1);
  const [genType, setGenType] = useState<GenType | ''>('');
  const [draft, setDraft] = useState<DraftFilter>('draft');
  // 画布「一键文生图」产出的素材任务（source=canvas_asset）默认不进画廊，
  // 否则一次批量会在草稿区刷出 N 个中间任务；但它们在别处没有入口，所以给个显式开关。
  const [showAssets, setShowAssets] = useState(false);
  // 批量模式
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showBatchPanel, setShowBatchPanel] = useState(false);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['tasks', page, genType, draft, showAssets],
    queryFn: () => listTasks({ page, size: PAGE_SIZE, genType, draft, includeAssets: showAssets }),
    // 兜底轮询：任务卡会给「进行中的那一条」自己拉详情 + 分段进度（5s），并在转终态
    // 时刷一次列表，所以列表整体放宽到 20s —— 只兜「卡片不在视口里 / 卡片没挂载」的情况。
    // interrupted 也按前端终态处理（与 TaskCard 的 TERMINAL_STATUSES 保持一致，
    // 否则被中断的任务会让列表一直轮询下去）。
    refetchInterval: (query) => {
      const list = query.state.data?.list;
      const hasActive = list?.some(
        (t) => !['completed', 'failed', 'expired', 'interrupted'].includes(t.status),
      );
      return hasActive ? 20000 : false;
    },
  });

  // useMemo 稳定引用：避免每次轮询 refetch 导致 BatchReworkPanel useEffect 重复触发
  const stableTaskIds = useMemo(
    () => (showBatchPanel ? [...selectedIds] : []),
    [showBatchPanel, selectedIds],
  );

  const tasks = data?.list ?? [];
  const total = data?.total ?? 0;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  const switchFilter = (key: GenType | '') => {
    setGenType(key);
    setPage(1);
  };

  const switchDraftFilter = (key: DraftFilter) => {
    setDraft(key);
    setPage(1);
  };

  const toggleBatchMode = () => {
    setBatchMode((prev) => {
      if (prev) {
        setSelectedIds(new Set());
      }
      return !prev;
    });
  };

  const toggleSelectTask = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const selectedPrompts: Record<number, string> = {};
  for (const t of tasks) {
    if (selectedIds.has(t.id)) {
      selectedPrompts[t.id] = t.prompt ?? '';
    }
  }

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

      {/* 生成类型 + 草稿/成品 二维筛选 + 批量模式 */}
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
        <span className="mx-1 h-4 w-px bg-slate-200" aria-hidden />
        {DRAFT_FILTERS.map((f) => {
          const active = f.key === draft;
          return (
            <button
              key={f.key}
              type="button"
              onClick={() => switchDraftFilter(f.key)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
                active
                  ? 'bg-amber-500 text-white shadow-sm'
                  : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
              }`}
            >
              {f.label}
            </button>
          );
        })}
        <span className="mx-1 h-4 w-px bg-slate-200" aria-hidden />
        <button
          type="button"
          onClick={toggleBatchMode}
          className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
            batchMode
              ? 'bg-emerald-600 text-white shadow-sm'
              : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
          }`}
        >
          <CheckSquare className="h-3.5 w-3.5" />
          {batchMode ? '退出批量' : '批量重生'}
        </button>
        <span className="mx-1 h-4 w-px bg-slate-200" aria-hidden />
        <button
          type="button"
          onClick={() => {
            setShowAssets((v) => !v);
            setPage(1);
          }}
          title="画布「一键文生图」产出的素材任务默认不进画廊（避免刷屏）；打开这里可以找回它们，也能删除"
          className={`rounded-full px-3.5 py-1.5 text-xs font-medium transition-colors ${
            showAssets
              ? 'bg-slate-700 text-white shadow-sm'
              : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
          }`}
        >
          {showAssets ? '隐藏画布素材' : '显示画布素材'}
        </button>
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
            {genType === '' && draft === '' ? '还没有历史作品' : '该筛选下暂无作品'}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {genType === '' && draft === ''
              ? '去创作页提交一条需求，成果会出现在这里'
              : '换个筛选条件看看，或去创作页生成一条'}
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
            <div key={task.id} className="relative">
              <TaskCard task={task} />
              {/* 批量模式选择遮罩 */}
              {batchMode && (
                <button
                  type="button"
                  onClick={() => toggleSelectTask(task.id)}
                  className={`absolute right-3 top-3 z-10 flex h-7 w-7 items-center justify-center rounded-full border-2 transition-all ${
                    selectedIds.has(task.id)
                      ? 'border-emerald-500 bg-emerald-500 text-white'
                      : 'border-slate-300 bg-white/90 text-transparent hover:border-emerald-400'
                  }`}
                >
                  <span
                    className={`text-[10px] font-bold ${
                      selectedIds.has(task.id) ? 'text-white' : 'text-slate-300'
                    }`}
                  >
                    ✓
                  </span>
                </button>
              )}
            </div>
          ))}
        </motion.div>
      )}

      {/* 批量操作栏 */}
      {batchMode && selectedIds.size > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="sticky bottom-4 z-20 mb-4 flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50/95 px-4 py-3 shadow-lg backdrop-blur"
        >
          <p className="text-xs font-medium text-emerald-800">
            已选 {selectedIds.size} 个任务
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setSelectedIds(new Set())}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-white"
            >
              清空
            </button>
            <button
              type="button"
              onClick={() => setShowBatchPanel(true)}
              disabled={selectedIds.size === 0}
              className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-purple-600 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition-all hover:from-violet-700 hover:to-purple-700 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
            >
              <Wand2 className="h-3.5 w-3.5" />
              段重生
            </button>
          </div>
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

      {/* 批量段重生面板 */}
      {showBatchPanel && (
        <BatchReworkPanel
          taskIds={stableTaskIds}
          taskPrompts={selectedPrompts}
          onClose={() => {
            setShowBatchPanel(false);
            setSelectedIds(new Set());
            setBatchMode(false);
          }}
          onChanged={refetch}
        />
      )}
    </main>
  );
}
