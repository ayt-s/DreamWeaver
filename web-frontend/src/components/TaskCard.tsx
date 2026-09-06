import { motion } from 'framer-motion';
import {
  Video,
  Image as ImageIcon,
  Trash2,
  Hourglass,
  RefreshCw,
  ListVideo,
  Archive,
  Clock,
  CheckCircle2,
  Film,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { GenType, TaskResponse, TaskStatus } from '../types/task';
import {
  parseImageUrls,
  finalVideoUrl,
  segmentVideoUrls,
  GEN_TYPE_LABEL,
  shortSessionId,
  cachedImageUrl,
  formatDuration,
} from '../types/task';
import { deleteTask, regenerateTask, setTaskDraft } from '../api/tasks';
import SegmentManager from './SegmentManager';
import SlideshowPanel from './SlideshowPanel';

interface TaskCardProps {
  task: TaskResponse;
}

type TaskState = 'completed' | 'failed' | 'running' | 'queued';

const TERMINAL_STATUSES: TaskStatus[] = ['completed', 'failed', 'expired'];

function stateOf(status: TaskStatus): TaskState {
  if (status === 'completed') return 'completed';
  if (status === 'failed') return 'failed';
  if (status === 'queued' || status === 'pending') return 'queued';
  return 'running';
}

const STATE_LABEL: Record<TaskState, string> = {
  completed: '完成',
  failed: '失败',
  running: '进行中',
  queued: '排队中',
};

const STATE_BADGE: Record<TaskState, string> = {
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  running: 'bg-amber-100 text-amber-700',
  queued: 'bg-sky-100 text-sky-700',
};

function stateIcon(state: TaskState, genType?: GenType): ReactNode {
  if (state === 'completed') {
    return genType === 'text_image' ? (
      <ImageIcon className="h-4 w-4 text-emerald-600" />
    ) : (
      <Video className="h-4 w-4 text-emerald-600" />
    );
  }
  if (state === 'failed') return <Trash2 className="h-4 w-4 text-red-600" />;
  return <Hourglass className="h-4 w-4 text-amber-600" />;
}

/** 主产物类型：文生图出图，其余出视频（image_video 图+视频都展示） */
function headlineIcon(genType?: GenType): ReactNode {
  return genType === 'text_image' ? (
    <ImageIcon className="h-5 w-5 text-white" />
  ) : (
    <Video className="h-5 w-5 text-white" />
  );
}

/**
 * 画廊卡片：展示单个历史生成任务及其产物（视频/图片）。
 * 终态任务提供「重新生成」「删除」管理操作。
 */
export default function TaskCard({ task }: TaskCardProps) {
  const state = stateOf(task.status);
  const imageUrls = parseImageUrls(task.imageUrls);
  const genType = task.genType ?? 'text_video';
  const queryClient = useQueryClient();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [segmentPanelOpen, setSegmentPanelOpen] = useState(false);
  const [slideshowOpen, setSlideshowOpen] = useState(false);

  /** 卡片标题：优先展示创作需求原文，缺失时才用「任务 #id」兜底 */
  const displayTitle = task.prompt?.trim() ? task.prompt.trim() : `任务 #${task.id}`;

  const isTerminal = TERMINAL_STATUSES.includes(task.status);
  const isDraft = task.isDraft === true;

  const refreshList = () => queryClient.invalidateQueries({ queryKey: ['tasks'] });

  const regenMutation = useMutation({
    mutationFn: () => regenerateTask(task.id),
    onSuccess: refreshList,
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteTask(task.id),
    onSuccess: refreshList,
  });

  // 草稿/成品切换：仅终态任务可切换（后端限制），切换后刷新画廊列表
  const draftMutation = useMutation({
    mutationFn: () => setTaskDraft(task.id, !isDraft),
    onSuccess: refreshList,
  });

  const handleDelete = () => {
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setConfirmingDelete(false);
    deleteMutation.mutate();
  };

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className={`overflow-hidden rounded-2xl border bg-white shadow-sm transition-shadow hover:shadow-md ${
        isDraft
          ? 'border-dashed border-amber-300 bg-amber-50/40'
          : 'border-slate-200'
      }`}
    >
      {/* Header */}
      <div className="flex items-start gap-3 p-5">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-purple-600">
          {headlineIcon(genType)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className="truncate text-sm font-semibold text-slate-900"
              title={displayTitle}
            >
              {displayTitle}
            </span>
            <span className="inline-flex shrink-0 items-center rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
              {GEN_TYPE_LABEL[genType] ?? genType}
            </span>
            {isDraft && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                <Archive className="h-2.5 w-2.5" />
                草稿
              </span>
            )}
            <span
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STATE_BADGE[state]}`}
            >
              {stateIcon(state, genType)}
              {STATE_LABEL[state]}
            </span>
          </div>
          <p className="mt-1 flex items-center gap-2 truncate text-xs text-slate-500" title={task.sessionId}>
            <span>会话 {shortSessionId(task.sessionId)}</span>
            {(() => {
              const d = formatDuration(task.completedAt, task.createdAt);
              return d ? (
                <span
                  title="从提交到生成完成的耗时（不含内容时长）"
                  className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600"
                >
                  <Clock className="h-2.5 w-2.5" />
                  耗时 {d}
                </span>
              ) : null;
            })()}
          </p>
          {task.errorMessage && (
            <p className="mt-2 line-clamp-2 text-xs text-red-600">
              {task.errorMessage}
            </p>
          )}
        </div>
      </div>

      {/* 图片产物（text_image / comic_video / image_video 首帧） */}
      {imageUrls.length > 0 && (
        <div className="border-t border-slate-100 p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {imageUrls.map((url, i) => (
              <div
                key={`${task.id}-img-${i}-${url}`}
                className="group relative overflow-hidden rounded-xl border border-slate-200 bg-slate-100"
              >
                <img
                  src={cachedImageUrl(url)}
                  alt={`任务 ${task.id} 图片 ${i + 1}`}
                  loading="lazy"
                  className="aspect-video w-full object-cover transition-transform group-hover:scale-105"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              </div>
            ))}
          </div>
          {/* 图片任务（文生图/漫剧）且有段配置：显示段重生按钮 */}
          {isTerminal && !!task.segmentsJson && (
            <div className="mt-3 flex items-center justify-end">
              <button
                type="button"
                onClick={() => setSegmentPanelOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 px-2.5 py-1.5 text-[11px] font-medium text-violet-600 transition-colors hover:bg-violet-50"
              >
                <ListVideo className="h-3.5 w-3.5" />
                段重生
              </button>
            </div>
          )}
          {/* 至少 2 张图才能拼成片 */}
          {isTerminal && imageUrls.length >= 2 && (
            <div className="mt-3 flex items-center justify-end">
              <button
                type="button"
                onClick={() => setSlideshowOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 px-2.5 py-1.5 text-[11px] font-medium text-emerald-600 transition-colors hover:bg-emerald-50"
              >
                <Film className="h-3.5 w-3.5" />
                合成视频
              </button>
            </div>
          )}
        </div>
      )}

      {/* 视频产物：画布模式成片优先单列，分段收起；标准模式平铺 */}
      {segmentVideoUrls(task.resultJson).length > 0 || finalVideoUrl(task.resultJson) ? (
        <div className="border-t border-slate-100 p-5">
          {(() => {
            const finalUrl = finalVideoUrl(task.resultJson);
            const segs = segmentVideoUrls(task.resultJson);
            // 标准模式（无拼接成片）：沿用平铺布局
            if (!finalUrl) {
              return (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {segs.map((url, i) => (
                    <div
                      key={`${task.id}-${i}-${url}`}
                      className="overflow-hidden rounded-xl border border-slate-200 bg-black"
                    >
                      <video src={url} controls preload="metadata" className="aspect-video w-full" />
                    </div>
                  ))}
                </div>
              );
            }
            // 画布模式：成片大居中 + 分段缩略图一排
            return (
              <div className="space-y-3">
                <div className="overflow-hidden rounded-xl border border-violet-300 bg-black">
                  <video src={finalUrl} controls preload="metadata" className="aspect-video w-full" />
                </div>
                <div className="flex items-center justify-between">
                  <p className="text-[11px] text-slate-400">
                    已拼接 {segs.length} 段
                  </p>
                  <button
                    type="button"
                    onClick={() => setSegmentPanelOpen(true)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 px-2.5 py-1.5 text-[11px] font-medium text-violet-600 transition-colors hover:bg-violet-50"
                  >
                    <ListVideo className="h-3.5 w-3.5" />
                    穿帮段重生
                  </button>
                </div>
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {segs.map((url, i) => (
                    <div
                      key={`${task.id}-seg-${i}-${url}`}
                      className="w-28 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-black"
                    >
                      <video
                        src={url}
                        preload="metadata"
                        muted
                        className="aspect-video w-full"
                        onClick={(e) => (e.currentTarget as HTMLVideoElement).play()}
                      />
                      <div className="bg-slate-900 px-1.5 py-0.5 text-center text-[9px] text-slate-300">
                        第 {i + 1} 段
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      ) : imageUrls.length === 0 && state === 'running' ? (
        <div className="flex items-center gap-2 border-t border-slate-100 px-5 py-4 text-xs text-slate-400">
          <Hourglass className="h-4 w-4 animate-pulse text-amber-500" />
          AI 导演正在创作中，稍后回来查看…
        </div>
      ) : (
        <div className="px-5 py-4 text-xs text-slate-400">暂无生成产物</div>
      )}

      {/* 管理操作：删任何任务（运行中会顺带取消 Agent 排期）；重新生成仅终态 */}
            <div className="flex items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
              {isTerminal && !task.segmentsJson && (
                <button
                  type="button"
                  onClick={() => regenMutation.mutate()}
                  disabled={regenMutation.isPending}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 px-3 py-1.5 text-xs font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 ${regenMutation.isPending ? 'animate-spin' : ''}`}
                  />
                  {regenMutation.isPending ? '提交中…' : '重新生成'}
                </button>
              )}
              {isTerminal && !!task.segmentsJson && (
                <button
                  type="button"
                  onClick={() => setSegmentPanelOpen(true)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 px-3 py-1.5 text-xs font-medium text-violet-600 transition-colors hover:bg-violet-50"
                >
                  <ListVideo className="h-3.5 w-3.5" />
                  穿帮段重生
                </button>
              )}
              {isTerminal && !!task.segmentsJson && (
                <button
                  type="button"
                  onClick={() => regenMutation.mutate()}
                  disabled={regenMutation.isPending}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 ${regenMutation.isPending ? 'animate-spin' : ''}`}
                  />
                  {regenMutation.isPending ? '提交中…' : '全量重生'}
                </button>
              )}
              {isTerminal && (
                <button
                  type="button"
                  onClick={() => draftMutation.mutate()}
                  disabled={draftMutation.isPending}
                  title={isDraft ? '确认成品（移到成品区）' : '退回草稿（回到草稿区）'}
                  className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    isDraft
                      ? 'border-green-300 bg-green-50 text-green-700 hover:bg-green-100'
                      : 'border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100'
                  }`}
                >
                  {isDraft ? (
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  ) : (
                    <Archive className="h-3.5 w-3.5" />
                  )}
                  {draftMutation.isPending
                    ? '切换中…'
                    : isDraft
                      ? '确认成品'
                      : '退回草稿'}
                </button>
              )}
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                  confirmingDelete
                    ? 'border-red-300 bg-red-50 text-red-600 hover:bg-red-100'
                    : 'border-slate-200 text-slate-500 hover:bg-slate-50'
                }`}
              >
                <Trash2 className="h-3.5 w-3.5" />
                {deleteMutation.isPending
                  ? '删除中…'
                  : confirmingDelete
                    ? '再次点击确认'
                    : '删除'}
              </button>
            </div>

            {/* 穿帮段重生面板（画布多段任务才可用） */}
            {segmentPanelOpen && (
              <SegmentManager
                taskId={task.id}
                onClose={() => setSegmentPanelOpen(false)}
                onChanged={refreshList}
              />
            )}

            {/* 图片合成视频面板（至少 2 张图） */}
            {slideshowOpen && (
              <SlideshowPanel
                task={task}
                onClose={() => setSlideshowOpen(false)}
                onChanged={refreshList}
              />
            )}
          </motion.article>
        );
      }