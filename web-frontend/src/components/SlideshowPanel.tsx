import { useState } from 'react';
import { motion } from 'framer-motion';
import { X, Film, Loader2, AlertCircle } from 'lucide-react';
import { createSlideshowTask } from '../api/tasks';
import { cachedImageUrl, parseImageUrls } from '../types/task';
import type { TaskResponse } from '../types/task';

interface SlideshowPanelProps {
  task: TaskResponse;
  onClose: () => void;
  /** 提交后回调（父组件刷新任务列表） */
  onChanged: () => void;
}

/**
 * 图片合成视频面板。
 *
 * 数据流：展示当前任务的图片 → 用户勾选要进入成片的图（可拖动顺序）→
 * 设置单张停留秒数 → 提交后 agent 用 ffmpeg 幻灯片拼接成片。
 * 不消耗 agnes 额度，比「逐张图生视频」快一个量级。
 */
export default function SlideshowPanel({ task, onClose, onChanged }: SlideshowPanelProps) {
  const allImages = parseImageUrls(task.imageUrls).filter(Boolean);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [slideSeconds, setSlideSeconds] = useState(3);
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');

  const toggle = (url: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  /** 把勾选图移到前一位/后一位（决定成片顺序） */
  const move = (url: string, dir: -1 | 1) => {
    setSelected((prev) => {
      const arr = Array.from(prev);
      const i = arr.indexOf(url);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= arr.length) return prev;
      [arr[i], arr[j]] = [arr[j], arr[i]];
      return new Set(arr);
    });
  };

  const handleSubmit = async () => {
    const images = Array.from(selected);
    if (images.length < 2) {
      setSubmitError('至少勾选 2 张图片才能合成视频');
      return;
    }
    setSubmitting(true);
    setSubmitError('');
    try {
      await createSlideshowTask({
        slideshowImages: images,
        slideSeconds,
        prompt: title.trim() || undefined,
      });
      onChanged();
      onClose();
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : '合成视频提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (allImages.length < 2) {
    return (
      <motion.div
        className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        onClick={onClose}
      >
        <div className="w-full max-w-md rounded-2xl bg-white p-6 text-center shadow-2xl">
          <p className="text-sm text-slate-500">该任务图片不足 2 张，无法合成视频</p>
          <button
            type="button"
            onClick={onClose}
            className="mt-4 rounded-lg border border-slate-200 px-4 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            关闭
          </button>
        </div>
      </motion.div>
    );
  }

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
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-100">
              <Film className="h-4 w-4 text-emerald-600" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">图片合成视频</h3>
              <p className="text-[11px] text-slate-500">
                勾选图片并按顺序拼接成一条成片，不消耗生成额度
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
          <label className="mb-1 block text-[10px] font-medium text-slate-500">成片标题（可选）</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="留空自动命名"
            maxLength={60}
            className="mb-3 w-full rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs focus:border-emerald-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-emerald-500/15"
          />

          <p className="mb-2 text-[10px] font-medium text-slate-500">
            勾选要进入成片的图片（至少 2 张，可调整顺序）
          </p>
          <div className="space-y-2">
            {allImages.map((url, i) => {
              const selIdx = Array.from(selected).indexOf(url);
              const isSel = selIdx >= 0;
              return (
                <div
                  key={url}
                  className={`flex items-center gap-3 rounded-xl border p-2.5 transition-colors ${
                    isSel
                      ? 'border-emerald-400 bg-emerald-50/40 ring-1 ring-emerald-400/30'
                      : 'border-slate-200 bg-white'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={isSel}
                    onChange={() => toggle(url)}
                    className="h-4 w-4 shrink-0 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                  />
                  <div className="h-10 w-16 shrink-0 overflow-hidden rounded-md border border-slate-200 bg-slate-100">
                    <img
                      src={cachedImageUrl(url)}
                      alt={`图片 ${i + 1}`}
                      className="h-full w-full object-cover"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold text-slate-700">
                      {isSel ? `第 ${selIdx + 1} 张` : `原图 ${i + 1}`}
                    </p>
                    <p className="truncate text-[10px] text-slate-400" title={url}>{url}</p>
                  </div>
                  {isSel && (
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        onClick={() => move(url, -1)}
                        disabled={selIdx === 0}
                        className="rounded-md border border-slate-200 px-1.5 py-1 text-[10px] text-slate-500 hover:bg-slate-50 disabled:opacity-30"
                      >
                        上移
                      </button>
                      <button
                        type="button"
                        onClick={() => move(url, 1)}
                        disabled={selIdx === selected.size - 1}
                        className="rounded-md border border-slate-200 px-1.5 py-1 text-[10px] text-slate-500 hover:bg-slate-50 disabled:opacity-30"
                      >
                        下移
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* 单张停留时长 */}
          <div className="mt-4">
            <div className="mb-1 flex items-center justify-between">
              <label className="text-[10px] font-medium text-slate-500">单张停留时长</label>
              <span className="text-[11px] font-semibold text-emerald-600">{slideSeconds}s</span>
            </div>
            <input
              type="range"
              min={1}
              max={10}
              step={1}
              value={slideSeconds}
              onChange={(e) => setSlideSeconds(Number(e.target.value))}
              className="w-full accent-emerald-600"
            />
            <p className="mt-1 text-[10px] text-slate-400">
              预计成片约 {Array.from(selected).length * slideSeconds}s（过渡会略微缩短）
            </p>
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
              已选 {selected.size} / {allImages.length} 张
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
                disabled={submitting || selected.size < 2}
                className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 px-4 py-2 text-xs font-medium text-white shadow-sm transition-all hover:from-emerald-700 hover:to-teal-700 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
              >
                {submitting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Film className="h-3.5 w-3.5" />
                )}
                {submitting ? '提交中…' : `合成视频（${selected.size} 张）`}
              </button>
            </div>
          </div>
          <p className="mt-2 flex items-center gap-1 text-[10px] text-slate-400">
            <Film className="h-3 w-3" />
            提交后生成新任务，画廊页自动刷新，无需手动操作。
          </p>
        </div>
      </motion.div>
    </motion.div>
  );
}
