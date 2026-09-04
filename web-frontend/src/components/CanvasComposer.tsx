import { useRef, useState } from 'react';
import {
  ImagePlus,
  Link as LinkIcon,
  Plus,
  Trash2,
  GripVertical,
  MoveRight,
  Film,
  ChevronUp,
  ChevronDown,
} from 'lucide-react';
import { cachedImageUrl, type CanvasSegment } from '../types/task';

interface CanvasComposerProps {
  segments: CanvasSegment[];
  onChange: (segments: CanvasSegment[]) => void;
  /** large = 独立页全屏画布（更宽卡片）；默认 normal = 紧凑卡片 */
  size?: 'normal' | 'large';
}

const SECONDS_OPTIONS = [4, 5, 6, 7, 8, 9, 10, 11, 12];

/**
 * 无限画布编辑器：图生视频的画布式输入端。
 *
 * 用户沿一条线依次摆放"片段卡"（一张参考图 + 一段视频内容描述），
 * 每段生成几秒小视频，模型侧 synthesizer 按顺序拼接成一条长视频。
 * 卡片支持增删与顺序调整。
 */
export default function CanvasComposer({
  segments,
  onChange,
  size = 'normal',
}: CanvasComposerProps) {
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null);
  const dragOverIndex = useRef<number | null>(null);
  const cardWidth = size === 'large' ? 'w-80' : 'w-60';

  const updateSegment = (index: number, patch: Partial<CanvasSegment>) => {
    onChange(segments.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  };

  const removeSegment = (index: number) => {
    onChange(segments.filter((_, i) => i !== index));
  };

  const addSegment = () => {
    onChange([...segments, { imageUrl: '', prompt: '', seconds: 5 }]);
  };

  const moveSegment = (from: number, to: number) => {
    if (to < 0 || to >= segments.length || from === to) return;
    const next = [...segments];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  const onDrop = (targetIndex: number) => {
    if (draggingIndex !== null && draggingIndex !== targetIndex) {
      moveSegment(draggingIndex, targetIndex);
    }
    setDraggingIndex(null);
    dragOverIndex.current = null;
  };

  const totalSeconds = segments.reduce((acc, s) => acc + (s.seconds || 5), 0);

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
      {/* 说明头 */}
      <div className="mb-3 flex items-center gap-2">
        <ImagePlus className="h-4 w-4 text-violet-500" />
        <span className="text-xs font-medium text-slate-600">无限画布</span>
        <span className="text-[10px] text-slate-400">
          沿一条线摆放片段，每段生成几秒小视频，自动拼接成一条长视频
        </span>
        <span className="ml-auto rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-medium text-violet-700">
          {segments.length} 段 · 约 {totalSeconds}s
        </span>
      </div>

      {segments.length === 0 ? (
        <div className="flex flex-col items-center rounded-lg border-2 border-dashed border-slate-200 py-8 text-center">
          <Film className="mb-2 h-8 w-8 text-slate-300" />
          <p className="text-xs text-slate-400">还没有片段</p>
          <p className="mt-1 text-[10px] text-slate-400">添加第一张图片开始创作你的长视频</p>
        </div>
      ) : (
        <div className="flex items-stretch gap-1 overflow-x-auto pb-2">
          {segments.map((seg, i) => (
            <div key={i} className="flex items-stretch gap-1">
              <div
                draggable
                onDragStart={() => setDraggingIndex(i)}
                onDragOver={(e) => {
                  e.preventDefault();
                  dragOverIndex.current = i;
                }}
                onDrop={() => onDrop(i)}
                onDragEnd={() => setDraggingIndex(null)}
                className={`${cardWidth} shrink-0 rounded-xl border bg-white p-3 shadow-sm transition-all ${
                  draggingIndex === i ? 'opacity-50' : ''
                } ${i === 0 ? 'border-violet-300' : 'border-slate-200'}`}
              >
                {/* 卡头：序号 + 拖拽 + 删除 */}
                <div className="mb-2 flex items-center gap-1.5">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-violet-100 text-[10px] font-bold text-violet-700">
                    {i + 1}
                  </span>
                  <GripVertical className="h-3.5 w-3.5 cursor-grab text-slate-300" />
                  <span className="text-[10px] text-slate-400">片段 {i + 1}</span>
                  <div className="ml-auto flex items-center gap-0.5">
                    <button
                      type="button"
                      onClick={() => moveSegment(i, i - 1)}
                      disabled={i === 0}
                      className="rounded p-1 text-slate-300 transition-colors hover:text-violet-500 disabled:cursor-not-allowed disabled:opacity-30"
                      aria-label={`片段 ${i + 1} 上移`}
                    >
                      <ChevronUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveSegment(i, i + 1)}
                      disabled={i === segments.length - 1}
                      className="rounded p-1 text-slate-300 transition-colors hover:text-violet-500 disabled:cursor-not-allowed disabled:opacity-30"
                      aria-label={`片段 ${i + 1} 下移`}
                    >
                      <ChevronDown className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => removeSegment(i)}
                      className="rounded p-1 text-slate-300 transition-colors hover:text-red-500"
                      aria-label={`删除片段 ${i + 1}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>

                {/* 图片 URL + 预览 */}
                <div className="relative mb-2 aspect-video overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
                  {seg.imageUrl ? (
                    <img
                      src={cachedImageUrl(seg.imageUrl)}
                      alt={`片段 ${i + 1} 参考图`}
                      className="h-full w-full object-cover"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center">
                      <ImagePlus className="h-6 w-6 text-slate-300" />
                    </div>
                  )}
                </div>

                <div className="relative mb-2">
                  <LinkIcon className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-slate-300" />
                  <input
                    type="text"
                    value={seg.imageUrl}
                    placeholder="粘贴图片 URL（公网可访问）"
                    onChange={(e) => updateSegment(i, { imageUrl: e.target.value })}
                    className="w-full rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-7 pr-2 text-[11px] focus:border-violet-400 focus:bg-white focus:outline-none"
                  />
                </div>

                <textarea
                  value={seg.prompt}
                  onChange={(e) => updateSegment(i, { prompt: e.target.value })}
                  placeholder="描述这段视频内容，如：镜头缓缓推近，海浪拍打礁石..."
                  rows={2}
                  className="mb-2 w-full resize-none rounded-lg border border-slate-200 bg-slate-50 p-2 text-[11px] leading-snug focus:border-violet-400 focus:bg-white focus:outline-none"
                />

                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-slate-400">时长</span>
                  <select
                    value={seg.seconds}
                    onChange={(e) => updateSegment(i, { seconds: Number(e.target.value) })}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-1.5 py-1 text-[11px] focus:border-violet-400 focus:outline-none"
                  >
                    {SECONDS_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {s} 秒
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* 连接线：片段之间的"一条线" */}
              {i < segments.length - 1 && (
                <div className="flex shrink-0 items-center">
                  <MoveRight className="h-4 w-4 text-violet-400" />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 添加片段 */}
      <button
        type="button"
        onClick={addSegment}
        className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-dashed border-violet-300 px-3 py-1.5 text-xs font-medium text-violet-600 transition-colors hover:bg-violet-50"
      >
        <Plus className="h-3.5 w-3.5" />
        添加片段
      </button>
    </div>
  );
}