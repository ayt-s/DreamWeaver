import { motion } from 'framer-motion';
import { Video, Image as ImageIcon, Trash2, Hourglass } from 'lucide-react';
import type { ReactNode } from 'react';
import type { GenType, TaskResponse, TaskStatus } from '../types/task';
import { parseResultUrls, parseImageUrls, GEN_TYPE_LABEL } from '../types/task';

interface TaskCardProps {
  task: TaskResponse;
}

type TaskState = 'completed' | 'failed' | 'running';

function stateOf(status: TaskStatus): TaskState {
  if (status === 'completed') return 'completed';
  if (status === 'failed') return 'failed';
  return 'running';
}

const STATE_LABEL: Record<TaskState, string> = {
  completed: '完成',
  failed: '失败',
  running: '进行中',
};

const STATE_BADGE: Record<TaskState, string> = {
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  running: 'bg-amber-100 text-amber-700',
};

function stateIcon(state: TaskState, genType?: GenType): ReactNode {
  if (state === 'completed') {
    return genType === 'novel_image' ? (
      <ImageIcon className="h-4 w-4 text-emerald-600" />
    ) : (
      <Video className="h-4 w-4 text-emerald-600" />
    );
  }
  if (state === 'failed') return <Trash2 className="h-4 w-4 text-red-600" />;
  return <Hourglass className="h-4 w-4 text-amber-600" />;
}

/** 主产物类型：novel_image 出图，其余出视频（image_video 图+视频都展示） */
function headlineIcon(genType?: GenType): ReactNode {
  return genType === 'novel_image' ? (
    <ImageIcon className="h-5 w-5 text-white" />
  ) : (
    <Video className="h-5 w-5 text-white" />
  );
}

/**
 * 画廊卡片：展示单个历史生成任务及其产物（视频/图片）。
 */
export default function TaskCard({ task }: TaskCardProps) {
  const state = stateOf(task.status);
  const videoUrls = parseResultUrls(task.resultJson);
  const imageUrls = parseImageUrls(task.imageUrls);
  const genType = task.genType ?? 'text_video';

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
            <span className="truncate text-sm font-semibold text-slate-900">
              任务 #{task.id}
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
          <p className="mt-1 truncate text-xs text-slate-500">
            会话 {task.sessionId}
          </p>
          {task.errorMessage && (
            <p className="mt-2 line-clamp-2 text-xs text-red-600">
              {task.errorMessage}
            </p>
          )}
        </div>
      </div>

      {/* 图片产物（novel_image / image_video 首帧） */}
      {imageUrls.length > 0 && (
        <div className="grid grid-cols-2 gap-3 border-t border-slate-100 p-5 sm:grid-cols-3">
          {imageUrls.map((url, i) => (
            <div
              key={`${task.id}-img-${i}-${url}`}
              className="group relative overflow-hidden rounded-xl border border-slate-200 bg-slate-100"
            >
              <img
                src={url}
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
    </motion.article>
  );
}