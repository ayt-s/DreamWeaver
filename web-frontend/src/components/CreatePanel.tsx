import { useForm } from 'react-hook-form';
import { useMutation } from '@tanstack/react-query';
import { createVideoTask } from '../api/tasks';
import { useTaskStore } from '../store/taskStore';

interface CreateForm {
  prompt: string;
}

/**
 * 创作工作台：输入一句话需求 → 提交 → 跟踪任务。
 */
export default function CreatePanel() {
  const setActiveTask = useTaskStore((s) => s.setActiveTask);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<CreateForm>({ defaultValues: { prompt: '' } });

  const mutation = useMutation({
    mutationFn: createVideoTask,
    onSuccess: (task) => setActiveTask(task.id),
  });

  const onSubmit = (values: CreateForm) => {
    mutation.mutate({ prompt: values.prompt.trim() });
    reset({ prompt: '' });
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <textarea
          className="w-full rounded-lg border border-slate-300 p-3 text-sm leading-relaxed focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
          placeholder="一句话描述你想创作的视频，例如：做一个赛博朋克风格的咖啡产品宣传视频，5秒"
          rows={4}
          {...register('prompt', {
            required: '请输入创作需求',
            maxLength: { value: 2000, message: '需求过长（≤2000字）' },
          })}
        />
        {errors.prompt && (
          <p className="text-sm text-red-600">{errors.prompt.message}</p>
        )}
        <button
          type="submit"
          disabled={isSubmitting || mutation.isPending}
          className="rounded-lg bg-brand px-6 py-2.5 text-sm font-medium text-white transition hover:bg-brand-dark disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {isSubmitting || mutation.isPending ? '开始创作…' : '开始创作'}
        </button>
        {mutation.isError && (
          <p className="text-sm text-red-600">
            {mutation.error instanceof Error ? mutation.error.message : '提交失败'}
          </p>
        )}
      </form>
    </div>
  );
}