import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import {
  X,
  Wand2,
  Loader2,
  AlertCircle,
  CheckCircle2,
  ListVideo,
  RefreshCw,
} from 'lucide-react';
import {
  getTaskSegments,
  batchReworkTasks,
  type TaskSegment,
  type BatchReworkItemReq,
} from '../api/tasks';
import { cachedImageUrl } from '../types/task';

interface BatchReworkPanelProps {
  /** 已选任务 ID 列表 */
  taskIds: number[];
  /** 每个任务的 prompt 摘要（用于标题） */
  taskPrompts: Record<number, string>;
  onClose: () => void;
  onChanged: () => void;
}

/**
 * 批量按段重生面板。
 *
 * 画廊页选中多个任务后打开此面板，每个任务独立显示段列表，
 * 用户勾选要重生的段（可修改提示词），一次性提交所有任务。
 * 后端逐个处理，一个失败不影响其他。
 */
export default function BatchReworkPanel({
  taskIds,
  taskPrompts,
  onClose,
  onChanged,
}: BatchReworkPanelProps) {
  const [segmentsMap, setSegmentsMap] = useState<
    Record<number, TaskSegment[]>
  >({});
  const [loadErrors, setLoadErrors] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Record<number, Set<number>>>({});
  const [editedPrompts, setEditedPrompts] = useState<
    Record<number, Record<string, string>>
  >({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  /**
   * 破图兜底：段缩略图加载失败时原先是把 img 隐掉（留空灰框），用户看不出「这张参考图拿不到了」。
   * 改成明确「图失效」占位 + 指回本面板的下一步（勾选该段重生）。key = 任务ID#段号。
   */
  const [brokenThumbs, setBrokenThumbs] = useState<Set<string>>(new Set());
  const markThumbBroken = (key: string) =>
    setBrokenThumbs((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
  const brokenThumbsForTask = (id: number) =>
    Array.from(brokenThumbs).filter((k) => k.startsWith(`${id}-`)).length;
  const [result, setResult] = useState<{
    total: number;
    success: number;
    failed: number;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      const segMap: Record<number, TaskSegment[]> = {};
      const errMap: Record<number, string> = {};
      const selMap: Record<number, Set<number>> = {};
      for (const id of taskIds) {
        selMap[id] = new Set();
        try {
          const segs = await getTaskSegments(id);
          segMap[id] = segs;
        } catch (e) {
          errMap[id] = e instanceof Error ? e.message : '加载段配置失败';
          segMap[id] = [];
        }
      }
      if (!cancelled) {
        setSegmentsMap(segMap);
        setLoadErrors(errMap);
        setSelected(selMap);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskIds]);

  const toggleSelect = (taskId: number, idx: number) => {
    setSelected((prev) => {
      const taskSel = new Set(prev[taskId] ?? new Set());
      const expKey = `${taskId}-${idx}`;
      if (taskSel.has(idx)) {
        taskSel.delete(idx);
        setExpanded((e) => {
          const n = new Set(e);
          n.delete(expKey);
          return n;
        });
      } else {
        taskSel.add(idx);
        setExpanded((e) => new Set(e).add(expKey));
      }
      return { ...prev, [taskId]: taskSel };
    });
  };

  const onPromptChange = (taskId: number, idx: number, prompt: string) => {
    setEditedPrompts((prev) => ({
      ...prev,
      [taskId]: { ...(prev[taskId] ?? {}), [String(idx)]: prompt },
    }));
  };

  const totalSelected = taskIds.reduce(
    (sum, id) => sum + (selected[id]?.size ?? 0),
    0,
  );

  const handleSubmit = async () => {
    if (totalSelected === 0) return;
    setSubmitting(true);
    setSubmitError('');
    setResult(null);
    try {
      const items: BatchReworkItemReq[] = [];
      for (const id of taskIds) {
        const sel = selected[id];
        if (sel && sel.size > 0) {
          items.push({
            taskId: id,
            reworkIndices: [...sel].sort((a, b) => a - b),
            editedPrompts:
              editedPrompts[id] && Object.keys(editedPrompts[id]).length > 0
                ? editedPrompts[id]
                : undefined,
          });
        }
      }
      const res = await batchReworkTasks(items);
      setResult({ total: res.total, success: res.success, failed: res.failed });
      onChanged();
      if (res.failed === 0) {
        onClose();
      }
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : '批量重新生成提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = totalSelected > 0 && !submitting && !loading;

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      onClick={onClose}
    >
      <motion.div
        className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        initial={{ opacity: 0, scale: 0.96, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-100">
              <ListVideo className="h-4 w-4 text-violet-600" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">
                批量按段重生
              </h3>
              <p className="text-[11px] text-slate-500">
                勾选要重生的段（可改提示词），其余段复用原视频
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex items-center justify-center py-10">
              <Loader2 className="mr-2 h-5 w-5 animate-spin text-violet-500" />
              <span className="text-sm text-slate-500">加载段配置…</span>
            </div>
          )}

          {!loading &&
            taskIds.map((id) => {
              const segs = segmentsMap[id] ?? [];
              const taskSel = selected[id] ?? new Set<number>();
              const err = loadErrors[id];
              const prompt = taskPrompts[id] ?? '';
              return (
                <div key={id} className="mb-4">
                  {/* 任务标题 */}
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-xs font-semibold text-slate-700">
                      #{id}
                    </span>
                    <span
                      className="truncate text-[11px] text-slate-500"
                      title={prompt}
                      style={
                        prompt ? { maxWidth: '240px' } : { display: 'none' }
                      }
                    >
                      {prompt || '（无提示词）'}
                    </span>
                    {taskSel.size > 0 && (
                      <span className="rounded-full bg-violet-100 px-1.5 py-0.5 text-[9px] font-medium text-violet-700">
                        {taskSel.size} 段
                      </span>
                    )}
                  </div>

                  {/* 错误提示 */}
                  {err && (
                    <div className="mb-2 flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
                      <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                      {err}
                    </div>
                  )}

                  {segs.length === 0 && !err && (
                    <p className="text-[11px] text-slate-400">
                      该任务没有段配置（非画布模式任务），无法按段重生
                    </p>
                  )}

                  {/* 48×32 的缩略图放不下整句话，所以在列表上方补一句：是什么 + 按哪个按钮能救 */}
                  {brokenThumbsForTask(id) > 0 && (
                    <p className="mb-1.5 rounded-lg bg-amber-50 px-2 py-1 text-[10px] leading-tight text-amber-700">
                      有 {brokenThumbsForTask(id)} 段的参考图加载失败（产物可能已被清理）；勾选这些段重生即可重新出图。
                    </p>
                  )}

                  {/* 段列表 */}
                  <div className="space-y-1.5">
                    {segs.map((seg) => {
                      const idx = seg.index ?? 0;
                      const isSel = taskSel.has(idx);
                      const expKey = `${id}-${idx}`;
                      const isExp = expanded.has(expKey);
                      return (
                        <div
                          key={idx}
                          className={`overflow-hidden rounded-xl border transition-colors ${
                            isSel
                              ? 'border-violet-400 bg-violet-50/40 ring-1 ring-violet-400/30'
                              : 'border-slate-200 bg-white'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 p-2">
                            <input
                              type="checkbox"
                              checked={isSel}
                              onChange={() => toggleSelect(id, idx)}
                              className="h-4 w-4 shrink-0 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
                            />
                            <div className="h-8 w-12 shrink-0 overflow-hidden rounded-md border border-slate-200 bg-slate-100">
                              {brokenThumbs.has(expKey) ? (
                                <div
                                  title="产物可能已被清理；勾选该段重生即可重新出图"
                                  className="flex h-full w-full flex-col items-center justify-center text-center"
                                >
                                  <span className="text-[9px] font-medium text-amber-700">
                                    图失效
                                  </span>
                                </div>
                              ) : seg.thumbnail ? (
                                <img
                                  src={cachedImageUrl(seg.thumbnail)}
                                  alt={`#${id} 第 ${idx + 1} 段`}
                                  className="h-full w-full object-cover"
                                  onError={() => markThumbBroken(expKey)}
                                />
                              ) : (
                                <div className="flex h-full items-center justify-center text-[9px] text-slate-400">
                                  无图
                                </div>
                              )}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-1.5">
                                <span className="text-[11px] font-semibold text-slate-700">
                                  第 {idx + 1} 段
                                </span>
                                {seg.seconds ? (
                                  <span className="text-[9px] text-slate-400">
                                    {seg.seconds}s
                                  </span>
                                ) : null}
                              </div>
                              <p
                                className="mt-0.5 truncate text-[10px] text-slate-500"
                                title={seg.prompt}
                              >
                                {seg.prompt || '（空提示词）'}
                              </p>
                            </div>
                          </div>
                          {/* 编辑提示词（勾选后展开） */}
                          {isExp && (
                            <div className="border-t border-slate-100 bg-white p-2">
                              <textarea
                                value={
                                  editedPrompts[id]?.[String(idx)] ??
                                  seg.prompt ??
                                  ''
                                }
                                onChange={(e) =>
                                  onPromptChange(id, idx, e.target.value)
                                }
                                rows={2}
                                placeholder={seg.prompt || '输入新的视频描述…'}
                                className="w-full rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs leading-relaxed focus:border-violet-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-violet-500/15"
                              />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
        </div>

        {/* Footer */}
        <div className="border-t border-slate-100 px-5 py-3.5">
          {submitError && (
            <p className="mb-2 flex items-center gap-1.5 text-xs text-red-600">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              {submitError}
            </p>
          )}
          {result && (
            <p
              className={`mb-2 flex items-center gap-1.5 text-xs ${
                result.failed === 0 ? 'text-green-600' : 'text-amber-600'
              }`}
            >
              {result.failed === 0 ? (
                <CheckCircle2 className="h-3.5 w-3.5" />
              ) : (
                <AlertCircle className="h-3.5 w-3.5" />
              )}
              成功 {result.success} / {result.total}
              {result.failed > 0 && ` · 失败 ${result.failed}`}
            </p>
          )}
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] text-slate-400">
              已选 {totalSelected} 段（共 {taskIds.length} 个任务）
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-slate-200 px-3.5 py-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-purple-600 px-4 py-2 text-xs font-medium text-white shadow-sm transition-all hover:from-violet-700 hover:to-purple-700 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
              >
                {submitting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wand2 className="h-3.5 w-3.5" />
                )}
                {submitting
                  ? '提交中…'
                  : `重生 ${totalSelected} 段并重新拼接`}
              </button>
            </div>
          </div>
          <p className="mt-2 flex items-center gap-1 text-[10px] text-slate-400">
            <RefreshCw className="h-3 w-3" />
            重生期间任务会转为「进行中」，画廊页自动刷新，无需手动操作。
          </p>
        </div>
      </motion.div>
    </motion.div>
  );
}
