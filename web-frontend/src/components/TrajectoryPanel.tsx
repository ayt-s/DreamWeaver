import { useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTaskEvents } from '../hooks/useTaskEvents';
import { getTask } from '../api/tasks';
import { agentTaskState, type QcReport } from '../api/agent';
import { useTaskStore } from '../store/taskStore';
import { parseResultUrls } from '../types/task';
import { motion, AnimatePresence } from 'framer-motion';
import { Video, CheckCircle, XCircle, Clock, AlertCircle, ScanSearch } from 'lucide-react';
import { statusLabel } from '../types/task';

const NODE_NAMES: Record<string, string> = {
  requirement_parser: '需求解析',
  script_writer: '剧本生成',
  storyboarder: '分镜拆解',
  canvas_storyboarder: '画布分镜',
  image_generator: '图像生成',
  video_generator: '视频生成',
  asset_fetch: '产物本地化',
  qc_checker: '质量检查',
  synthesizer: '多镜拼接',
  notify_final: '任务收尾',
  fix_looping: '修复重试',
};

const EVENT_NAMES: Record<string, string> = {
  session_started: '会话开始',
  node_entered: '进入节点',
  node_completed: '节点完成',
  tool_called: '调用工具',
  tool_result: '工具返回',
  progress: '进度更新',
  interrupted: '流程中断',
  completed: '创作完成',
  failed: '创作失败',
  // 服务端事件缓冲被 ring 淘汰、客户端漏掉了一段时补发的提示（F2）。
  // 必须展示：静默缺一段轨迹是最难排查的那类问题。
  replay_gap: '轨迹有缺口',
};

export default function TrajectoryPanel() {
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const addCompletedTask = useTaskStore((s) => s.addCompletedTask);

  const { data: task, isLoading } = useQuery({
    queryKey: ['task', activeTaskId],
    queryFn: () => (activeTaskId != null ? getTask(activeTaskId) : null),
    refetchInterval: activeTaskId != null ? 3000 : false,
    enabled: activeTaskId != null,
  });

  // SSE 订阅用 FastAPI 侧 sessionId（task 返回后才有；未返回前不订阅）
  const sseSessionId = task?.sessionId ?? null;
  const { events, connected } = useTaskEvents(sseSessionId);

  const videoUrls = parseResultUrls(task?.resultJson);
  const recordedIds = useRef(new Set<number>());

  // 任务完成时记录到历史（每个 id 只记一次，避免轮询重复）
  const isDone = task?.status === 'completed' || task?.status === 'failed';
  // 中断任务在前端视为已停下：不再显示「Agent 正在创作中…」的假进行中提示；
  // 但仍保留 3s 轮询，以便后端迟到的 completed 回调把它复活时能自动刷出来。
  const isHalted = isDone || task?.status === 'interrupted';

  // 逐镜质检明细：只在 agent 的 state 里，Java 任务详情只带一句汇总（notify_final 写入）。
  // 任务停下后不再轮询（QC 结果不会再变）；agent 未启动/会话过期时静默为 null。
  const { data: agentState } = useQuery({
    queryKey: ['agentState', sseSessionId],
    queryFn: () => (sseSessionId ? agentTaskState(sseSessionId) : null),
    refetchInterval: sseSessionId && !isHalted ? 3000 : false,
    enabled: sseSessionId != null,
    retry: false,
  });
  const qcReport = agentState?.qc_report ?? null;
  if (isDone && task && !recordedIds.current.has(task.id)) {
    recordedIds.current.add(task.id);
    addCompletedTask(task);
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed': return <CheckCircle className="h-5 w-5 text-emerald-500" />;
      case 'failed': return <XCircle className="h-5 w-5 text-red-500" />;
      case 'queued': return <Clock className="h-5 w-5 text-amber-500 animate-pulse" />;
      // 中断是停下来的状态，用静态图标（掉进 default 会变成一直转圈的假「进行中」）
      case 'interrupted': return <AlertCircle className="h-5 w-5 text-amber-500" />;
      default: return <Video className="h-5 w-5 text-violet-500 animate-pulse" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-emerald-50 border-emerald-200 text-emerald-700';
      case 'failed': return 'bg-red-50 border-red-200 text-red-700';
      case 'queued': return 'bg-amber-50 border-amber-200 text-amber-700';
      case 'interrupted': return 'bg-amber-50 border-amber-200 text-amber-700';
      default: return 'bg-violet-50 border-violet-200 text-violet-700';
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.1 }}
      className="mt-6 rounded-2xl border border-slate-200 bg-white p-8 shadow-lg"
    >
      <div className="mb-6 flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-slate-100 to-slate-200">
          <Video className="h-5 w-5 text-slate-600" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-900">创作轨迹</h2>
          <p className="text-xs text-slate-500">实时跟踪 AI 导演的工作进度</p>
        </div>
        {task && (
          <span className={`ml-auto flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${getStatusColor(task.status)}`}>
            {getStatusIcon(task.status)}
            {statusLabel(task.status)}
            {!connected && <AlertCircle className="h-3 w-3" />}
          </span>
        )}
      </div>

      {!activeTaskId && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center py-12 text-center"
        >
          <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-slate-100">
            <Video className="h-8 w-8 text-slate-400" />
          </div>
          <p className="text-sm text-slate-500">提交任务后，这里会实时显示创作过程</p>
        </motion.div>
      )}

      {isLoading && (
        <div className="flex items-center justify-center py-8">
          <Clock className="h-6 w-6 animate-spin text-violet-500" />
          <span className="ml-2 text-sm text-slate-500">加载任务状态...</span>
        </div>
      )}

      {task && !isLoading && (
        <>
          <TaskStatusLine task={task} />
          <QcReportBlock qc={qcReport} />

          <div className="mt-6 space-y-2">
            {events.map((ev, i) => (
              <motion.li
                key={ev.eventId || i}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.05 }}
                className="flex items-center gap-3 rounded-lg border border-slate-100 bg-slate-50 px-4 py-2 text-sm"
              >
                {ev.type === 'node_entered' && <span className="text-violet-500">▶</span>}
                {ev.type === 'node_completed' && <span className="text-emerald-500">✓</span>}
                {ev.type === 'tool_called' && <span className="text-amber-500">🔧</span>}
                {ev.type === 'completed' && <CheckCircle className="h-4 w-4 text-emerald-500" />}
                {ev.type === 'failed' && <XCircle className="h-4 w-4 text-red-500" />}
                {ev.type === 'interrupted' && <AlertCircle className="h-4 w-4 text-amber-500" />}
                <span className="text-slate-700">
                  {NODE_NAMES[ev.data.nodeId ?? ''] ?? ev.data.nodeName ?? EVENT_NAMES[ev.type] ?? ev.type}
                </span>
                {ev.data.progress != null && (
                  <span className="ml-auto text-xs text-slate-400">{ev.data.progress}%</span>
                )}
              </motion.li>
            ))}
          </div>

          {!events.length && !isHalted && (
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="mt-4 text-sm text-slate-500"
            >
              Agent 正在创作中…（轨迹数据将在生成过程中实时更新）
            </motion.p>
          )}
        </>
      )}

      <AnimatePresence>
        {videoUrls.length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-6 overflow-hidden"
          >
            <div className="mb-3 flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-emerald-500" />
              <p className="text-sm font-medium text-slate-700">生成结果</p>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {videoUrls.map((url, i) => (
                <motion.div
                  key={`${i}-${url}`}
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: i * 0.1 }}
                  className="overflow-hidden rounded-xl border border-slate-200 bg-black"
                >
                  <video
                    src={url}
                    controls
                    className="w-full"
                  />
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/**
 * 逐镜质检结果（A7）。
 *
 * 数据来源是 agent 的 `/v1/tasks/{id}`（`qc_report`），不是 Java 的任务详情 ——
 * Java 侧只有 `notify_final` 写入的一句汇总（`error_message`），
 * 逐镜原因（哪一镜、什么原因）只有 agent 知道。
 *
 * `qc` 为 null 表示**没跑质检**（图片任务 / 合成视频），与「质检通过」是两件事，
 * 所以此处不渲染任何东西，而不是渲染「全部通过」。
 */
export function QcReportBlock({ qc }: { qc: QcReport | null }) {
  if (!qc) return null;

  const failed = qc.failed_shots ?? [];
  const total = qc.total_shots ?? qc.shots?.length ?? 0;
  const passedCount = total - failed.length;
  const ok = qc.passed;

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      data-testid="qc-report"
      className={
        'mb-4 rounded-lg border px-4 py-3 text-sm ' +
        (ok
          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
          : 'border-amber-200 bg-amber-50 text-amber-800')
      }
    >
      <div className="flex items-center gap-2">
        <ScanSearch className="h-5 w-5 shrink-0" />
        <span className="font-medium">
          质检：{passedCount}/{total} 镜通过
        </span>
      </div>
      {!ok && failed.length > 0 && (
        <ul className="mt-2 space-y-1 pl-7 text-xs">
          {failed.map((idx) => {
            const shot = (qc.shots ?? []).find((s) => s.index === idx);
            return (
              <li key={idx}>
                第 {idx + 1} 镜：{shot?.error || '未通过'}
                {shot?.duration != null && shot.duration > 0 && (
                  <span className="text-amber-600/80">
                    （实测 {shot.duration.toFixed(1)}s
                    {shot.duration_expected != null ? ` / 期望 ${shot.duration_expected}s` : ''}）
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </motion.div>
  );
}

function TaskStatusLine({ task }: { task: { status: string; errorMessage?: string } }) {
  if (task.status === 'failed') {
    return (
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
      >
        <XCircle className="h-5 w-5 shrink-0" />
        失败：{task.errorMessage ?? '未知原因'}
      </motion.div>
    );
  }
  if (task.status === 'completed') {
    return (
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-4 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700"
      >
        <CheckCircle className="h-5 w-5 shrink-0" />
        视频生成完成！
      </motion.div>
    );
  }
  if (task.status === 'interrupted') {
    return (
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-4 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700"
      >
        <AlertCircle className="h-5 w-5 shrink-0" />
        任务已中断，Agent 将在后台自动恢复续跑，稍后刷新即可看到最新进展。
      </motion.div>
    );
  }
  return null;
}
