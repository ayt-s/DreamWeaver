import { AnimatePresence, motion } from 'framer-motion';
import CreatePanel from '../components/CreatePanel';
import TrajectoryPanel from '../components/TrajectoryPanel';
import HistoryPanel from '../components/HistoryPanel';
import { useTaskStore } from '../store/taskStore';

/**
 * 创作页：左侧输入需求，右侧实时轨迹 + 历史作品。
 */
export default function CreatePage() {
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const completedTasks = useTaskStore((s) => s.completedTasks);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8 text-center"
      >
        <h1 className="bg-gradient-to-r from-violet-600 to-purple-600 bg-clip-text text-4xl font-bold text-transparent">
          DreamWeaver
        </h1>
        <p className="mt-2 text-sm text-slate-500">
          AI 导演 Agent — 一句话需求 → 自动产出短视频成片
        </p>
        <div className="mt-3 flex justify-center gap-2">
          <span className="rounded-full bg-violet-100 px-3 py-1 text-xs font-medium text-violet-700">
            LangGraph + FastAPI + Spring Boot
          </span>
          <span className="rounded-full bg-purple-100 px-3 py-1 text-xs font-medium text-purple-700">
            React 18 + TypeScript
          </span>
        </div>
      </motion.div>

      {/* Main Layout */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Left: Create Panel */}
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.1 }}
          className="space-y-6"
        >
          <CreatePanel />

          {/* Active Task Status */}
          {activeTaskId && (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="rounded-xl border border-violet-200 bg-violet-50 p-4"
            >
              <div className="flex items-center gap-2">
                <div className="h-2 w-2 animate-pulse rounded-full bg-violet-500" />
                <span className="text-sm font-medium text-violet-700">
                  任务 #{activeTaskId} 进行中
                </span>
              </div>
              <p className="mt-1 text-xs text-violet-500">
                右侧面板显示实时创作轨迹和最终结果
              </p>
            </motion.div>
          )}
        </motion.div>

        {/* Right: Trajectory + History */}
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.2 }}
          className="space-y-6"
        >
          <TrajectoryPanel />
          <AnimatePresence>
            {completedTasks.length > 0 && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
              >
                <HistoryPanel tasks={completedTasks} />
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>
    </main>
  );
}
