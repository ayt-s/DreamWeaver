import { motion } from 'framer-motion';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { createVideoTask } from '../api/tasks';
import { enrichPrompt } from '../api/enrich';
import { useTaskStore } from '../store/taskStore';
import type { GenType } from '../types/task';
import {
  SHOT_SIZE_OPTIONS,
  CAMERA_ANGLE_OPTIONS,
  CAMERA_MOVE_OPTIONS,
} from '../types/task';
import {
  Sparkles,
  Loader2,
  Zap,
  Image,
  Clapperboard,
  LayoutGrid,
  Wand2,
  Settings2,
  ChevronDown,
  Video,
  type LucideIcon,
} from 'lucide-react';

interface CreateForm {
  prompt: string;
  genType: GenType;
  stylePrompt: string;
  negativePrompt: string;
  totalSeconds: string;
  shotCount: string;
  shotSize: string;
  cameraAngle: string;
  cameraMove: string;
}

const FORM_DEFAULTS: CreateForm = {
  prompt: '',
  genType: 'text_video',
  stylePrompt: '',
  negativePrompt: '',
  totalSeconds: '',
  shotCount: '',
  shotSize: '',
  cameraAngle: '',
  cameraMove: '',
};

const SUGGESTIONS = [
  '赛博朋克风格的咖啡产品宣传视频，5秒',
  '一只猫在太空漫步的科幻短片，10秒',
  '中国风山水画的动态视觉效果，8秒',
];

const GEN_TYPE_OPTIONS: { value: GenType; label: string; desc: string; icon: LucideIcon }[] = [
  { value: 'text_image', label: '文生图', desc: '文字描述直接生成图片', icon: Image },
  { value: 'text_video', label: '文生视频', desc: '文字描述直接生成视频', icon: Clapperboard },
  { value: 'image_video', label: '图生视频', desc: '独立画布页：加图+描述生成片段，一线串成长视频', icon: LayoutGrid },
];

const PLACEHOLDER: Record<GenType, string> = {
  text_video: '描述你想创作的视频内容...',
  image_video: '在画布中添加片段（图片 + 视频内容描述），模型会自动拼接成长视频...',
  text_image: '描述你想生成的画面，如：赛博朋克城市夜景，霓虹灯牌...',
};

export default function CreatePanel() {
  const navigate = useNavigate();
  const setActiveTask = useTaskStore((s) => s.setActiveTask);
  const {
    register,
    handleSubmit,
    reset,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<CreateForm>({ defaultValues: FORM_DEFAULTS });

  const mutation = useMutation({
    mutationFn: createVideoTask,
    onSuccess: (task) => {
      setActiveTask(task.id);
      reset(FORM_DEFAULTS);
    },
  });

  const genType = watch('genType');
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [enriching, setEnriching] = useState(false);
  const [enrichError, setEnrichError] = useState('');

  // AI 丰富提示词：仅文生图/文生视频可用（图生视频走画布页）
  const isEnrichable = genType === 'text_image' || genType === 'text_video';
  // 时间轴适用范围：标准视频给总时长+镜头数；文生图只给分镜张数
  const isVideoTimeline = genType === 'text_video';
  // ★ 2026-09-19 修（#34）：原来还有 `|| genType === 'comic_video'` —— 一个**前端零生产者**
  //   的不可达类型（类型定义已删，见 types/task.ts）。删掉后「镜头数」参数的适用面没变。
  const isShotCountApplicable = genType === 'text_video' || genType === 'text_image';
  const onEnrich = async () => {
    const current = watch('prompt').trim();
    if (!current) {
      setEnrichError('请先输入创作描述');
      return;
    }
    setEnriching(true);
    setEnrichError('');
    try {
      const enriched = await enrichPrompt(current, genType as 'text_image' | 'text_video');
      setValue('prompt', enriched);
    } catch (e) {
      setEnrichError(e instanceof Error ? e.message : 'AI 丰富失败，请重试');
    } finally {
      setEnriching(false);
    }
  };

  const onSubmit = (values: CreateForm) => {
    const prompt = values.prompt.trim();
    // 时间轴：空串 → undefined（不传，让 LLM 自由决定）；非法数字同理
    const totalSeconds = Number.parseInt(values.totalSeconds, 10);
    const shotCount = Number.parseInt(values.shotCount, 10);
    // 全局运镜倾向：三项全空 → undefined（保持 LLM 自由分镜）
    const shotLanguage: Record<string, string> = {};
    if (values.shotSize) shotLanguage.shot_size = values.shotSize;
    if (values.cameraAngle) shotLanguage.angle = values.cameraAngle;
    if (values.cameraMove) shotLanguage.movement = values.cameraMove;
    mutation.mutate({
      prompt,
      genType: values.genType,
      stylePrompt: values.stylePrompt.trim() || undefined,
      negativePrompt: values.negativePrompt.trim() || undefined,
      totalSeconds: Number.isFinite(totalSeconds) && totalSeconds > 0 ? totalSeconds : undefined,
      shotCount: Number.isFinite(shotCount) && shotCount > 0 ? shotCount : undefined,
      shotLanguage: Object.keys(shotLanguage).length > 0
        ? JSON.stringify(shotLanguage)
        : undefined,
    });
  };

  const fillSuggestion = (text: string) => {
    reset({ ...FORM_DEFAULTS, prompt: text, genType });
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
        {/* 生成类型选择 */}
        <div className="grid grid-cols-3 gap-2">
          {GEN_TYPE_OPTIONS.map((opt) => {
            const isCanvas = opt.value === 'image_video';
            const active = genType === opt.value;
            const Icon = opt.icon;
            return (
            <button
              key={opt.value}
              type="button"
              onClick={() => {
                if (isCanvas) {
                  navigate('/canvas');
                  return;
                }
                setValue('genType', opt.value);
              }}
              className={`group rounded-xl border px-3 py-2.5 text-left transition-all ${
                isCanvas
                  ? 'border-transparent bg-gradient-to-br from-violet-600 via-indigo-600 to-cyan-500 shadow-md hover:shadow-lg hover:scale-[1.02]'
                  : active
                    ? 'border-violet-500 bg-gradient-to-br from-violet-50 to-indigo-50 ring-2 ring-violet-500/20'
                    : 'border-slate-200 bg-white hover:border-violet-300 hover:bg-violet-50/60'
              }`}
            >
              <span
                className={`flex items-center gap-1.5 text-xs font-semibold ${
                  isCanvas ? 'text-white' : active ? 'text-violet-700' : 'text-slate-700'
                }`}
              >
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md transition-colors ${
                    isCanvas
                      ? 'bg-white/25 text-white'
                      : active
                        ? 'bg-violet-600 text-white'
                        : 'bg-violet-100 text-violet-600 group-hover:bg-violet-200'
                  }`}
                >
                  <Icon className="h-3 w-3" />
                </span>
                {opt.label}
                {isCanvas && (
                  <span className="rounded-full bg-white/25 px-1.5 py-0.5 text-[9px] font-medium text-white">
                    画布入口
                  </span>
                )}
              </span>
              <span
                className={`mt-0.5 block text-[10px] leading-tight ${
                  isCanvas ? 'text-white/85' : 'text-slate-400'
                }`}
              >
                {opt.desc}
              </span>
            </button>
            );
          })}
        </div>

        {/* 文本描述（画布模式下作为补充/汇总） */}
        <div className="relative">
          <textarea
            className={`w-full rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm leading-relaxed focus:border-violet-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-violet-500/10 transition-all ${
              isEnrichable ? 'pr-32' : ''
            }`}
            placeholder={PLACEHOLDER[genType]}
            rows={genType === 'image_video' ? 3 : 4}
            {...register('prompt', {
              required: genType !== 'image_video' ? '请输入创作需求' : false,
              maxLength: { value: 2000, message: '需求过长（≤2000字）' },
            })}
          />
          {isEnrichable && (
            <button
              type="button"
              onClick={onEnrich}
              disabled={enriching}
              className="absolute right-3 top-3 z-10 inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-purple-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-all hover:from-violet-700 hover:to-purple-700 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
              title="AI 根据你的描述生成更丰富的创作提示词"
            >
              {enriching ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Wand2 className="h-3.5 w-3.5" />
              )}
              {enriching ? '丰富中...' : 'AI 丰富'}
            </button>
          )}
          {errors.prompt && (
            <p className="mt-2 text-sm text-red-600">{errors.prompt.message}</p>
          )}
          {enrichError && !errors.prompt && (
            <p className="mt-2 text-sm text-red-600">{enrichError}</p>
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

        {/* 高级设置：风格 / 负面提示词 / 时间轴（可灵式精细控制） */}
        <div className="rounded-xl border border-slate-200 bg-slate-50/60">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="flex w-full items-center justify-between px-4 py-2.5 text-left"
          >
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-600">
              <Settings2 className="h-3.5 w-3.5" />
              高级设置
              <span className="font-normal text-slate-400">风格 · 负面词 · 时间轴</span>
            </span>
            <ChevronDown
              className={`h-4 w-4 text-slate-400 transition-transform ${advancedOpen ? 'rotate-180' : ''}`}
            />
          </button>

          {advancedOpen && (
            <div className="space-y-3 border-t border-slate-200 px-4 py-3">
              {/* ① 风格提示词 */}
              <div>
                <label className="mb-1 block text-[11px] font-medium text-slate-500">
                  风格提示词
                  <span className="ml-1 font-normal text-slate-400">
                    画面质感 / 光影 / 渲染风格，会折进每一镜
                  </span>
                </label>
                <textarea
                  rows={2}
                  placeholder="如：3D写实国漫风，虚幻5，OC渲染，电影级光影，体积光雾，色调柔和富有层次"
                  className="w-full rounded-lg border border-slate-200 bg-white p-2.5 text-xs leading-relaxed focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10"
                  {...register('stylePrompt', { maxLength: { value: 1000, message: '风格过长（≤1000字）' } })}
                />
              </div>

              {/* ① 负面提示词 */}
              <div>
                <label className="mb-1 block text-[11px] font-medium text-slate-500">
                  负面提示词
                  <span className="ml-1 font-normal text-slate-400">
                    要规避的穿帮，会以「避免出现：…」写进提示词
                  </span>
                </label>
                <textarea
                  rows={2}
                  placeholder="如：人物肢体扭曲、手指畸形、面部崩坏、穿模、画面抖动闪烁、水印logo、多余肢体"
                  className="w-full rounded-lg border border-slate-200 bg-white p-2.5 text-xs leading-relaxed focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10"
                  {...register('negativePrompt', { maxLength: { value: 1000, message: '负面词过长（≤1000字）' } })}
                />
              </div>

              {/* ② 时间轴：标准视频给「总时长+镜头数」；文生图只出图，仅给镜头数（分镜张数） */}
              {isShotCountApplicable && (
                <div className={`grid gap-3 ${isVideoTimeline ? 'grid-cols-2' : 'grid-cols-1'}`}>
                  {isVideoTimeline && (
                    <div>
                      <label className="mb-1 block text-[11px] font-medium text-slate-500">
                        总时长（秒）
                      </label>
                      <input
                        type="number"
                        min={4}
                        max={600}
                        placeholder="留空=自动"
                        className="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10"
                        {...register('totalSeconds', {
                          min: { value: 4, message: '总时长至少 4 秒' },
                          max: { value: 600, message: '总时长最多 600 秒' },
                        })}
                      />
                    </div>
                  )}
                  <div>
                    <label className="mb-1 block text-[11px] font-medium text-slate-500">
                      {isVideoTimeline ? '镜头数' : '分镜张数'}
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      placeholder="留空=自动"
                      className="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10"
                      {...register('shotCount', {
                        min: { value: 1, message: '镜头数至少 1' },
                        max: { value: 20, message: '镜头数最多 20' },
                      })}
                    />
                  </div>
                  <p className="col-span-full text-[10px] leading-relaxed text-slate-400">
                    {isVideoTimeline
                      ? '两者都填时按「总时长 ÷ 镜头数」精确分配每镜秒数（单镜 4~12 秒，超出会自动钳制）；只填其一则另一项交给模型决定。'
                      : '指定要生成几张分镜图；留空由模型按内容决定。'}
                  </p>
                </div>
              )}
              {(errors.totalSeconds || errors.shotCount) && (
                <p className="text-[11px] text-red-600">
                  {errors.totalSeconds?.message || errors.shotCount?.message}
                </p>
              )}

              {/* ③ 全局运镜倾向：仅标准文生视频（LLM 自由分镜）适用 */}
              {genType === 'text_video' && (
                <div>
                  <label className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
                    <Video className="h-3 w-3" />
                    运镜倾向
                    <span className="font-normal text-slate-400">
                      留空 = 由模型按内容自由分镜；选定后覆盖每一镜
                    </span>
                  </label>
                  <div className="grid grid-cols-3 gap-2">
                    <select
                      className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10"
                      {...register('shotSize')}
                    >
                      <option value="">景别：自动</option>
                      {SHOT_SIZE_OPTIONS.map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                    <select
                      className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10"
                      {...register('cameraAngle')}
                    >
                      <option value="">机位：自动</option>
                      {CAMERA_ANGLE_OPTIONS.map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                    <select
                      className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10"
                      {...register('cameraMove')}
                    >
                      <option value="">运镜：自动</option>
                      {CAMERA_MOVE_OPTIONS.map((v) => (
                        <option key={v} value={v}>{v}</option>
                      ))}
                    </select>
                  </div>
                </div>
              )}
            </div>
          )}
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