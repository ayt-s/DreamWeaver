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
  SlidersHorizontal,
  AlertTriangle,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
import {
  concatTask,
  deleteTask,
  regenerateTask,
  setTaskDraft,
  getTask,
  getTaskSegments,
} from '../api/tasks';
import { useTaskEvents } from '../hooks/useTaskEvents';
import SegmentManager from './SegmentManager';
import SlideshowPanel from './SlideshowPanel';
import ParamEditDialog from './ParamEditDialog';

interface TaskCardProps {
  task: TaskResponse;
  /**
   * 是否订阅这条任务的 SSE 事件。**由列表决定**（只给最近几个进行中的任务订阅）：
   * 同域 HTTP/1.1 连接数上限约 6，一页 10 个活动任务全订阅会占满连接、还会与轨迹面板互抢。
   */
  subscribe?: boolean;
}

type TaskState = 'completed' | 'failed' | 'running' | 'queued' | 'interrupted';

/**
 * 终态状态集合：终态任务才提供「重新生成 / 按段重生 / 确认成品」等操作。
 *
 * interrupted（已中断）**在前端按终态处理**——后端允许迟到的 completed 回调把它复活，
 * 但从用户视角任务已经停下来了；当成非终态会让用户盯着一个转圈的卡片却点不了任何按钮。
 * 「前端当终态、后端当可复活」是有意的不对称。
 */
const TERMINAL_STATUSES: TaskStatus[] = ['completed', 'failed', 'expired', 'interrupted'];

function stateOf(status: TaskStatus): TaskState {
  if (status === 'completed') return 'completed';
  if (status === 'failed') return 'failed';
  if (status === 'interrupted') return 'interrupted';
  if (status === 'queued' || status === 'pending') return 'queued';
  return 'running';
}

const STATE_LABEL: Record<TaskState, string> = {
  completed: '完成',
  failed: '失败',
  running: '进行中',
  queued: '排队中',
  interrupted: '已中断',
};

const STATE_BADGE: Record<TaskState, string> = {
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  running: 'bg-amber-100 text-amber-700',
  queued: 'bg-sky-100 text-sky-700',
  interrupted: 'bg-amber-100 text-amber-700',
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
  // 中断不用闪烁的 Hourglass，避免被误读成「还在跑」
  if (state === 'interrupted') return <AlertTriangle className="h-4 w-4 text-amber-600" />;
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
export default function TaskCard({ task, subscribe = false }: TaskCardProps) {
  const state = stateOf(task.status);
  const imageUrls = parseImageUrls(task.imageUrls);
  const genType = task.genType ?? 'text_video';
  const queryClient = useQueryClient();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [segmentPanelOpen, setSegmentPanelOpen] = useState(false);
  const [slideshowOpen, setSlideshowOpen] = useState(false);
  const [paramEditOpen, setParamEditOpen] = useState(false);

  /** 卡片标题：优先展示创作需求原文，缺失时才用「任务 #id」兜底 */
  const displayTitle = task.prompt?.trim() ? task.prompt.trim() : `任务 #${task.id}`;

  const isTerminal = TERMINAL_STATUSES.includes(task.status);
  const isDraft = task.isDraft === true;

  const refreshList = () => queryClient.invalidateQueries({ queryKey: ['tasks'] });

  // 只给「这一条进行中的任务」拉详情 + 分段进度（5s）。
  // 列表整体因此可以从 5s 放宽到 20s 兜底：卡片状态不再依赖列表刷新，
  // 而且转终态那一刻会自己刷一次列表，就地翻成「完成/失败」。
  const live = !TERMINAL_STATUSES.includes(task.status);
  // SSE：有订阅时进度是推来的（不用等轮询），并把详情轮询放宽到 15s 兜底。
  // ⚠️ 必须声明在下面的 useQuery 之前 —— refetchInterval 在渲染期求值，
  // 放到后面会因 TDZ 直接报错。
  const { events: sseEvents, connected: sseConnected } = useTaskEvents(
    subscribe && live ? task.sessionId : null,
  );
  const lastEvent = sseEvents.length ? sseEvents[sseEvents.length - 1] : null;
  const lastData = (lastEvent?.data ?? {}) as {
    phase?: string;
    node_name?: string;
    nodeName?: string;
    summary?: string;
    progress?: number;
  };
  const livePhase = lastData.phase || lastData.node_name || lastData.nodeName || lastData.summary || '';
  const liveProgress = typeof lastData.progress === 'number' ? lastData.progress : undefined;

  const { data: taskLive } = useQuery({
    queryKey: ['task', task.id],
    queryFn: () => getTask(task.id),
    enabled: live,
    // SSE 已连接时靠推送 + 15s 兜底；没连接就还是 5s 轮询
    refetchInterval: live ? (sseConnected ? 15000 : 5000) : false,
  });
  const { data: liveSegs } = useQuery({
    queryKey: ['task-segments', task.id],
    queryFn: () => getTaskSegments(task.id),
    enabled: live,
    refetchInterval: live ? 5000 : false,
  });
  const totalSegs = liveSegs?.length ?? 0;
  // 段「已完成」的判定：视频任务看 existing_video_url，图片任务看 existing_image_url
  const doneSegs =
    liveSegs?.filter((s) => s.existing_video_url || s.existing_image_url).length ?? 0;
  useEffect(() => {
    if (taskLive && TERMINAL_STATUSES.includes(taskLive.status)) {
      refreshList();
    }
    // refreshList 每次渲染都是新函数（放依赖里会反复触发），这里只看状态跃迁
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskLive?.status]);
  useEffect(() => {
    // SSE 直接收到终态事件 → 立刻刷列表（不必等下次兜底轮询）
    if (lastEvent && (lastEvent.type === 'completed' || lastEvent.type === 'failed')) {
      refreshList();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent]);

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

  // 拼接成片：标准模式（无成片）的分段视频，一键拼成一条长视频
  // 不消耗生成额度（后端跑本地 ffmpeg），成功后列表刷新即变成「成片 + 分段缩略」布局
  const concatMutation = useMutation({
    mutationFn: () => concatTask(task.id),
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
            {subscribe && live && livePhase && (
              <span
                title="SSE 实时事件（列表只订阅最近几个进行中的任务）"
                className="inline-flex shrink-0 items-center gap-1 rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-700"
              >
                {livePhase}
                {liveProgress !== undefined ? ` ${liveProgress}%` : ''}
              </span>
            )}
            {live && totalSegs > 0 && (
              <span
                title="这条任务已完成的分段（每 5s 更新；列表整体已放宽到 20s 兜底轮询）"
                className="inline-flex shrink-0 items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600"
              >
                <ListVideo className="h-2.5 w-2.5" />
                已完成 {doneSegs}/{totalSegs} 段
              </span>
            )}
          </div>
          <p className="mt-1 flex items-center gap-2 truncate text-xs text-slate-500" title={task.sessionId}>
            <span>会话 {shortSessionId(task.sessionId)}</span>
            {(() => {
              // 起点优先取生成打点（Agent 受理时刻）→ 得到实际生成耗时；
              // 无打点（历史数据/中断后迟到完成）才回退提交时间，并在 title 里说明口径差异
              const startedAt = task.startedAt ?? task.createdAt;
              const d = formatDuration(task.completedAt, startedAt);
              return d ? (
                <span
                  title={
                    task.startedAt
                      ? '从 Agent 受理到生成完成的实际生成耗时（不含排队与中断等待，不含内容时长）'
                      : '从提交到完成的总历时（含排队与中断等待；该任务缺少生成打点）'
                  }
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
          {/* 图片任务（文生图/漫剧）段重生按钮：无分镜数据的旧任务置灰并说明原因 */}
          {isTerminal && (
            <div className="mt-3 flex items-center justify-end">
              <button
                type="button"
                onClick={() => setSegmentPanelOpen(true)}
                disabled={!task.segmentsJson}
                title={
                  task.segmentsJson
                    ? '勾选要重生的图片，其余图片复用原图'
                    : '该任务未保存分镜数据，仅支持全量重生'
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 px-2.5 py-1.5 text-[11px] font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300 disabled:hover:bg-transparent"
              >
                <ListVideo className="h-3.5 w-3.5" />
                按段重生
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
                <div className="space-y-3">
                  {/* 多段时给「拼成一条」的入口：此前只有画布模式自动拼接，标准模式只能平铺看 */}
                  {isTerminal && segs.length >= 2 && (
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-[11px] text-slate-400">
                        {segs.length} 段独立视频，可按顺序拼成一条成片
                      </p>
                      <button
                        type="button"
                        onClick={() => concatMutation.mutate()}
                        disabled={concatMutation.isPending}
                        title="按顺序用交叉淡化过渡拼成一条长视频；纯本地 ffmpeg，不消耗生成额度"
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-violet-200 px-2.5 py-1.5 text-[11px] font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300 disabled:hover:bg-transparent"
                      >
                        <Film className="h-3.5 w-3.5" />
                        {concatMutation.isPending ? '拼接中…' : '拼接成片'}
                      </button>
                    </div>
                  )}
                  {concatMutation.isError && (
                    <p className="text-[11px] text-red-600">
                      {concatMutation.error instanceof Error
                        ? concatMutation.error.message
                        : '拼接失败，请稍后重试'}
                    </p>
                  )}
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
                    disabled={!task.segmentsJson}
                    title={
                      task.segmentsJson
                        ? '勾选要重生的段，其余段复用原视频'
                        : '该任务未保存分镜数据，仅支持全量重生'
                    }
                    className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 px-2.5 py-1.5 text-[11px] font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300 disabled:hover:bg-transparent"
                  >
                    <ListVideo className="h-3.5 w-3.5" />
                    按段重生
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
      ) : imageUrls.length === 0 && state === 'interrupted' ? (
        <div className="flex items-center gap-2 border-t border-slate-100 px-5 py-4 text-xs text-amber-600">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />
          任务已中断，Agent 将在后台自动恢复续跑，可稍后刷新查看…
        </div>
      ) : (
        <div className="px-5 py-4 text-xs text-slate-400">暂无生成产物</div>
      )}

      {/* 管理操作：删任何任务（运行中会顺带取消 Agent 排期）；重新生成仅终态 */}
            <div className="flex items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
              {/* 编辑参数：查看/修改该任务保存的精细控制参数，改完原地重新生成 */}
              {isTerminal && (
                <button
                  type="button"
                  onClick={() => setParamEditOpen(true)}
                  title="查看并修改该任务保存的风格提示词 / 负面词 / 运镜 / 时长 / 镜头数"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:border-violet-300 hover:bg-violet-50 hover:text-violet-600"
                >
                  <SlidersHorizontal className="h-3.5 w-3.5" />
                  编辑参数
                </button>
              )}
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
              {/* 按段重生：需要任务保存了分镜（segments_json）；旧任务置灰并说明原因 */}
              {isTerminal && (
                <button
                  type="button"
                  onClick={() => setSegmentPanelOpen(true)}
                  disabled={!task.segmentsJson}
                  title={
                    task.segmentsJson
                      ? '勾选要重生的段，其余段复用原视频'
                      : '该任务未保存分镜数据，仅支持全量重生'
                  }
                  className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 px-3 py-1.5 text-xs font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300 disabled:hover:bg-transparent"
                >
                  <ListVideo className="h-3.5 w-3.5" />
                  按段重生
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

            {/* 按段重生面板（画布多段任务才可用） */}
            {segmentPanelOpen && (
              <SegmentManager
                taskId={task.id}
                genType={task.genType}
                onClose={() => setSegmentPanelOpen(false)}
                onChanged={refreshList}
              />
            )}

            {/* 参数编辑弹窗：改完精细控制参数后原地重新生成 */}
            {paramEditOpen && (
              <ParamEditDialog
                task={task}
                onClose={() => setParamEditOpen(false)}
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