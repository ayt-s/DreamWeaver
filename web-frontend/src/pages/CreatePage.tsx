import CreatePanel from '../components/CreatePanel';
import TrajectoryPanel from '../components/TrajectoryPanel';

/**
 * 创作页：左侧输入需求，右侧实时轨迹。
 */
export default function CreatePage() {
  return (
    <main className="create-page">
      <h1>DreamWeaver — AI 短视频创作</h1>
      <CreatePanel />
      <TrajectoryPanel />
    </main>
  );
}