import { useState } from 'react';
import { createVideoTask } from '../api/tasks';
import { useTaskStore } from '../store/taskStore';

/**
 * 创作工作台：输入一句话需求 → 提交 → 跟踪任务。
 */
export default function CreatePanel() {
  const [prompt, setPrompt] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const setActiveTask = useTaskStore((s) => s.setActiveTask);

  const onSubmit = async () => {
    if (!prompt.trim()) return;
    setSubmitting(true);
    setError('');
    try {
      const task = await createVideoTask({ prompt: prompt.trim() });
      setActiveTask(task.id);
      setPrompt('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="create-panel">
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder="一句话描述你想创作的视频，例如：做一个赛博朋克风格的咖啡产品宣传视频，5秒"
        rows={4}
      />
      <button onClick={onSubmit} disabled={submitting || !prompt.trim()}>
        {submitting ? '提交中…' : '开始创作'}
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}