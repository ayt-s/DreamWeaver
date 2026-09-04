import { motion } from 'framer-motion';
import { Video, Image as ImageIcon, Trash2, Hourglass, RefreshCw } from 'lucide-react';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { GenType, TaskResponse, TaskStatus } from '../types/task';
import {
  parseResultUrls,
  parseImageUrls,
  GEN_TYPE_LABEL,
  shortSessionId,
  cachedImageUrl,
} from '../types/task';
import { deleteTask, regenerateTask } from '../api/tasks';

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
  const videoUrls = parseResultUrls(task.resultJson);
  const imageUrls = parseImageUrls(task.imageUrls);
  const genType = task.genType ?? 'text_video';
  const queryClient = useQueryClient();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  /** 卡片标题：优先展示创作需求原文，缺失时才用「任务 #id」兜底 */
  const displayTitle = task.prompt?.trim() ? task.prompt.trim() : `任务 #${task.id}`;

  const isTerminal = TERMINAL_STATUSES.includes(task.status);

  const refreshList = () => queryClient.invalidateQueries({ queryKey: ['tasks'] });

  const regenMutation = useMutation({
    mutationFn: () => regenerateTask(task.id),
    onSuccess: refreshList,
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteTask(task.id),
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
      className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition-shadow hover:shadow-md"
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
            <span
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${STATE_BADGE[state]}`}
            >
              {stateIcon(state, genType)}
              {STATE_LABEL[state]}
            </span>
          </div>
          <p className="mt-1 truncate text-xs text-slate-500" title={task.sessionId}>
            会话 {shortSessionId(task.sessionId)}
          </p>
          {task.errorMessage && (
            <p className="mt-2 line-clamp-2 text-xs text-red-600">
              {task.errorMessage}
            </p>
          )}
        </div>
      </div>

      {/* 图片产物（text_image / image_video 首帧） */}
      {imageUrls.length > 0 && (
        <div className="grid grid-cols-2 gap-3 border-t border-slate-100 p-5 sm:grid-cols-3">
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
      )}

      {/* 视频产物 */}
      {videoUrls.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 border-t border-slate-100 p-5 sm:grid-cols-2">
          {videoUrls.map((url, i) => (
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
              {isTerminal && (
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
          </motion.article>
        );
      }