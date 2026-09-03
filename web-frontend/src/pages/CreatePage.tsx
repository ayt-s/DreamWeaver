import CreatePanel from '../components/CreatePanel';
import TrajectoryPanel from '../components/TrajectoryPanel';

/**
 * 创作页：左侧输入需求，右侧实时轨迹。
 */
export default function CreatePage() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-bold text-slate-900">
        DreamWeaver — AI 短视频创作
      </h1>
      <p className="mb-6 text-sm text-slate-500">
        一句话需求 → AI 导演 Agent 自动完成「剧本 → 分镜 → 生成 → 质检」
      </p>
      <CreatePanel />
      <TrajectoryPanel />
    </main>
  );
}