import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { X, Wand2, Loader2, AlertCircle, ListVideo, RefreshCw } from 'lucide-react';
import { getTaskSegments, reworkTask, type TaskSegment } from '../api/tasks';
import { cachedImageUrl } from '../types/task';
import type { GenType } from '../types/task';

interface SegmentManagerProps {
  taskId: number;
  onClose: () => void;
  /** 重生提交后回调（父组件刷新任务列表） */
  onChanged: () => void;
  /** 预加载的段配置（TaskCard 已加载时传入，避免重复请求） */
  segments?: TaskSegment[] | null;
  /** 生成类型：决定 UI 是图片重生还是视频重生 */
  genType?: GenType;
}

/**
 * 按段重生面板。
 *
 * 数据流：加载任务段配置 + 每段已有产物 → 用户勾选要重生的段（可修改提示词）→
 * 提交后 agent 只重生勾选段、复用其余段、重新拼接成片。
 * 后端容错：段数少于历史产物数时按索引尽力对齐，缺少可复用产物的段自动补入重生列表。
 */
export default function SegmentManager({ taskId, onClose, onChanged, segments: segmentsProp, genType }: SegmentManagerProps) {
  // 图片类任务（文生图 / 漫画）与视频类任务的段重生语义不同：前者重生单张图片，后者重生片段并拼接
  const isImageTask = genType === 'text_image' || genType === 'comic_video';
  const [segments, setSegments] = useState<TaskSegment[] | null>(segmentsProp ?? null);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editedPrompts, setEditedPrompts] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');

  const toggleSelect = (idx: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
        setExpanded((e) => {
          const n = new Set(e);
          n.delete(idx);
          return n;
        });
      } else {
        next.add(idx);
        setExpanded((e) => new Set(e).add(idx));
      }
      return next;
    });
  };

  const onPromptChange = (idx: number, prompt: string) => {
    setEditedPrompts((prev) => ({ ...prev, [String(idx)]: prompt }));
  };

  const handleSubmit = async () => {
    if (!segments || selected.size === 0) return;
    setSubmitting(true);
    setSubmitError('');
    try {
      await reworkTask(taskId, {
        reworkIndices: [...selected].sort((a, b) => a - b),
        editedPrompts: Object.keys(editedPrompts).length > 0 ? editedPrompts : undefined,
      });
      onChanged();
      onClose();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : '重新生成提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    // 如果父组件已传入 segments，直接使用（避免重复请求）
    if (segmentsProp && segmentsProp.length > 0) {
      setSegments(segmentsProp);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const segs = await getTaskSegments(taskId);
        if (!cancelled) setSegments(segs);
      } catch (e) {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : '加载段配置失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId, segmentsProp]);

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      onClick={onClose}
    >
      <motion.div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
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
              <h3 className="text-sm font-semibold text-slate-900">按段重生</h3>
              <p className="text-[11px] text-slate-500">
                {isImageTask
                  ? '勾选要重生的图片（可改提示词），其余图片复用原图'
                  : '勾选要重生的段（可改提示词），其余段复用原视频'}
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
          {loadError && (
            <div className="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2.5 text-xs text-red-600">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {loadError}
            </div>
          )}
          {segments && segments.length === 0 && (
            <p className="py-8 text-center text-xs text-slate-400">
              该任务没有段配置（非画布模式任务），无法按段重生
            </p>
          )}
          <div className="space-y-2.5">
            {segments?.map((seg) => {
              const idx = seg.index ?? 0;
              const isSel = selected.has(idx);
              const isExp = expanded.has(idx);
              return (
                <div
                  key={idx}
                  className={`overflow-hidden rounded-xl border transition-colors ${
                    isSel ? 'border-violet-400 bg-violet-50/40 ring-1 ring-violet-400/30' : 'border-slate-200 bg-white'
                  }`}
                >
                  <div className="flex items-center gap-3 p-2.5">
                    <input
                      type="checkbox"
                      checked={isSel}
                      onChange={() => toggleSelect(idx)}
                      className="h-4 w-4 shrink-0 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
                    />
                    {/* 缩略图：段首张参考图 */}
                    <div className="h-10 w-16 shrink-0 overflow-hidden rounded-md border border-slate-200 bg-slate-100">
                      {seg.thumbnail ? (
                        <img
                          src={cachedImageUrl(seg.thumbnail)}
                          alt={`第 ${idx + 1} 段`}
                          className="h-full w-full object-cover"
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = 'none';
                          }}
                        />
                      ) : (
                        <div className="flex h-full items-center justify-center text-[10px] text-slate-400">
                          无图
                        </div>
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold text-slate-700">
                          {isImageTask ? `第 ${idx + 1} 张` : `第 ${idx + 1} 段`}
                        </span>
                        {seg.seconds ? (
                          <span className="text-[10px] text-slate-400">{seg.seconds}s</span>
                        ) : null}
                        {isSel && (
                          <span className="rounded-full bg-violet-100 px-1.5 py-0.5 text-[9px] font-medium text-violet-700">
                            将重生
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 truncate text-[11px] text-slate-500" title={seg.prompt}>
                        {seg.prompt || '（空提示词，将用默认运镜）'}
                      </p>
                    </div>
                  </div>
                  {/* 编辑提示词（勾选后展开） */}
                  {isExp && (
                    <div className="border-t border-slate-100 bg-white p-2.5">
                      <label className="mb-1 block text-[10px] font-medium text-slate-500">
                        {isImageTask ? '图片提示词（留空则沿用原提示词）' : '视频提示词（留空则沿用原提示词）'}
                      </label>
                      <textarea
                        value={editedPrompts[String(idx)] ?? seg.prompt ?? ''}
                        onChange={(e) => onPromptChange(idx, e.target.value)}
                        rows={2}
                        placeholder={seg.prompt || (isImageTask ? '输入新的图片描述…' : '输入新的视频描述…')}
                        className="w-full rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs leading-relaxed focus:border-violet-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-violet-500/15"
                      />
                      <p className="mt-1 text-[10px] text-slate-400">
                        修改提示词可解决「描述不清导致的画面不符」；仅重试则留空。
                      </p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-slate-100 px-5 py-3.5">
          {submitError && (
            <p className="mb-2 flex items-center gap-1.5 text-xs text-red-600">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              {submitError}
            </p>
          )}
          <div className="flex items-center justify-between gap-3">
            <p className="text-[11px] text-slate-400">
              {segments ? `已选 ${selected.size} / ${segments.length} 段` : '加载中…'}
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
                disabled={submitting || selected.size === 0 || !segments}
                className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-purple-600 px-4 py-2 text-xs font-medium text-white shadow-sm transition-all hover:from-violet-700 hover:to-purple-700 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
              >
                {submitting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Wand2 className="h-3.5 w-3.5" />
                )}
                {submitting ? '提交中…' : isImageTask ? `重生 ${selected.size} 张图片` : `重生 ${selected.size} 段并重新拼接`}
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
