import { motion, AnimatePresence } from 'framer-motion';
import { useForm } from 'react-hook-form';
import { useMutation } from '@tanstack/react-query';
import { createVideoTask } from '../api/tasks';
import { useTaskStore } from '../store/taskStore';
import { Sparkles, Loader2, Zap } from 'lucide-react';

interface CreateForm {
  prompt: string;
}

const SUGGESTIONS = [
  '赛博朋克风格的咖啡产品宣传视频，5秒',
  '一只猫在太空漫步的科幻短片，10秒',
  '中国风山水画的动态视觉效果，8秒',
];

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
    onSuccess: (task) => {
      setActiveTask(task.id);
      reset({ prompt: '' });
    },
  });

  const onSubmit = (values: CreateForm) => {
    mutation.mutate({ prompt: values.prompt.trim() });
  };

  const fillSuggestion = (text: string) => {
    reset({ prompt: text });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="rounded-2xl border border-slate-200 bg-white p-8 shadow-lg"
    >
      <div className="mb-6 flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-purple-600">
          <Sparkles className="h-5 w-5 text-white" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-slate-900">开始创作</h2>
          <p className="text-xs text-slate-500">AI 导演 Agent 将自动完成全流程</p>
        </div>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div className="relative">
          <textarea
            className="w-full rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm leading-relaxed focus:border-violet-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-violet-500/10 transition-all"
            placeholder="描述你想创作的视频内容..."
            rows={4}
            {...register('prompt', {
              required: '请输入创作需求',
              maxLength: { value: 2000, message: '需求过长（≤2000字）' },
            })}
          />
          {errors.prompt && (
            <p className="mt-2 text-sm text-red-600">{errors.prompt.message}</p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <span className="text-xs text-slate-400 self-center">推荐：</span>
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => fillSuggestion(s)}
              className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-violet-300 hover:text-violet-600 transition-colors"
            >
              {s.slice(0, 12)}...
            </button>
          ))}
        </div>

        <motion.button
          type="submit"
          disabled={isSubmitting || mutation.isPending}
          whileHover={{ scale: isSubmitting ? 1 : 1.02 }}
          whileTap={{ scale: isSubmitting ? 1 : 0.98 }}
          className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-purple-600 px-8 py-3 text-sm font-medium text-white shadow-md hover:shadow-lg disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300 transition-all"
        >
          {isSubmitting || mutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              AI 导演工作中...
            </>
          ) : (
            <>
              <Zap className="h-4 w-4" />
              开始创作
            </>
          )}
        </motion.button>

        {mutation.isError && (
          <motion.p
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-sm text-red-600"
          >
            {mutation.error instanceof Error ? mutation.error.message : '提交失败，请重试'}
          </motion.p>
        )}
      </form>
    </motion.div>
  );
}
