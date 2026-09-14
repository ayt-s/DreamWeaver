import { motion } from 'framer-motion';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Settings2, X, RefreshCw, Loader2, Video } from 'lucide-react';
import type { TaskResponse } from '../types/task';
import {
  SHOT_SIZE_OPTIONS,
  CAMERA_ANGLE_OPTIONS,
  CAMERA_MOVE_OPTIONS,
} from '../types/task';
import { regenerateTask, type RegenerateParams } from '../api/tasks';

interface ParamEditDialogProps {
  task: TaskResponse;
  onClose: () => void;
  /** 提交成功后的额外回调（画廊列表刷新由弹窗内部完成） */
  onChanged?: () => void;
}

/**
 * 卡片上的任务可能带 gen_params_json（后端 TaskResponse.genParamsJson）。
 * 这里用交叉类型读取，避免与 types/task.ts 的字段定义改动相互耦合。
 */
type TaskWithGenParams = TaskResponse & { genParamsJson?: string };

interface ParamForm {
  stylePrompt: string;
  negativePrompt: string;
  shotSize: string;
  cameraAngle: string;
  cameraMove: string;
  totalSeconds: string;
  shotCount: string;
}

const EMPTY_FORM: ParamForm = {
  stylePrompt: '',
  negativePrompt: '',
  shotSize: '',
  cameraAngle: '',
  cameraMove: '',
  totalSeconds: '',
  shotCount: '',
};

/**
 * 用任务已保存的 gen_params_json 预填表单（null / 解析失败 → 全部留空）。
 * shotLanguage 是内嵌的 JSON 字符串，需二次解析成三个下拉的初值。
 */
export function parseGenParams(raw?: string | null): ParamForm {
  if (!raw) return EMPTY_FORM;
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>;
    let camera: { shot_size?: unknown; angle?: unknown; movement?: unknown } = {};
    if (typeof obj.shotLanguage === 'string' && obj.shotLanguage.trim()) {
      try {
        camera = JSON.parse(obj.shotLanguage) as typeof camera;
      } catch {
        camera = {};
      }
    }
    const asStr = (v: unknown) =>
      v === null || v === undefined || v === '' ? '' : String(v);
    return {
      stylePrompt: asStr(obj.stylePrompt),
      negativePrompt: asStr(obj.negativePrompt),
      shotSize: asStr(camera.shot_size),
      cameraAngle: asStr(camera.angle),
      cameraMove: asStr(camera.movement),
      totalSeconds: asStr(obj.totalSeconds),
      shotCount: asStr(obj.shotCount),
    };
  } catch {
    return EMPTY_FORM;
  }
}

/**
 * 任务参数编辑弹窗：查看并修改历史任务的精细控制参数，
 * 提交时带着新参数原地重新生成（后端用新参数覆盖 entity 里保存的旧参数）。
 *
 * 空值语义：留空/不选 = 不改这一项（沿用任务里已保存的值），与后端「非空字段才覆盖」一致。
 */
export default function ParamEditDialog({ task, onClose, onChanged }: ParamEditDialogProps) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ParamForm>(() =>
    parseGenParams((task as TaskWithGenParams).genParamsJson),
  );

  const mutation = useMutation({
    mutationFn: (params: RegenerateParams) => regenerateTask(task.id, params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      onChanged?.();
      onClose();
    },
  });

  const setField = (key: keyof ParamForm) => (value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const handleSubmit = () => {
    const totalSeconds = Number.parseInt(form.totalSeconds, 10);
    const shotCount = Number.parseInt(form.shotCount, 10);
    // 运镜：三项全空 → 不传（保持任务已有的运镜倾向）
    const shotLanguage: Record<string, string> = {};
    if (form.shotSize) shotLanguage.shot_size = form.shotSize;
    if (form.cameraAngle) shotLanguage.angle = form.cameraAngle;
    if (form.cameraMove) shotLanguage.movement = form.cameraMove;

    mutation.mutate({
      stylePrompt: form.stylePrompt.trim() || undefined,
      negativePrompt: form.negativePrompt.trim() || undefined,
      totalSeconds:
        Number.isFinite(totalSeconds) && totalSeconds > 0 ? totalSeconds : undefined,
      shotCount: Number.isFinite(shotCount) && shotCount > 0 ? shotCount : undefined,
      shotLanguage: Object.keys(shotLanguage).length > 0
        ? JSON.stringify(shotLanguage)
        : undefined,
    });
  };

  const labelCls = 'mb-1 block text-[11px] font-medium text-slate-500';
  const hintCls = 'ml-1 font-normal text-slate-400';
  const textareaCls =
    'w-full rounded-lg border border-slate-200 bg-white p-2.5 text-xs leading-relaxed focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10';
  const inputCls =
    'w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10';
  const selectCls =
    'rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-500/10';

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      onClick={onClose}
    >
      <motion.div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        initial={{ opacity: 0, scale: 0.96, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-100">
              <Settings2 className="h-4 w-4 text-violet-600" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">编辑参数</h3>
              <p className="text-[11px] text-slate-500">
                修改后点「重新生成」，新参数会覆盖该任务已保存的设定
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
          <div className="rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
            创作需求（prompt）不在此处修改：重新生成沿用任务原文
            <span className="mx-1 text-slate-300">|</span>
            留空 = 不改这一项，沿用已保存的值
          </div>

          {/* ① 风格提示词 */}
          <div>
            <label className={labelCls}>
              风格提示词
              <span className={hintCls}>画面质感 / 光影 / 渲染风格，会折进每一镜</span>
            </label>
            <textarea
              rows={2}
              value={form.stylePrompt}
              onChange={(e) => setField('stylePrompt')(e.target.value)}
              placeholder="如：3D写实国漫风，虚幻5，OC渲染，电影级光影"
              className={textareaCls}
            />
          </div>

          {/* ② 负面提示词 */}
          <div>
            <label className={labelCls}>
              负面提示词
              <span className={hintCls}>要规避的穿帮，会以「避免出现：…」写进提示词</span>
            </label>
            <textarea
              rows={2}
              value={form.negativePrompt}
              onChange={(e) => setField('negativePrompt')(e.target.value)}
              placeholder="如：肢体扭曲、手指畸形、面部崩坏、穿模、水印logo"
              className={textareaCls}
            />
          </div>

          {/* ③ 运镜倾向（选项与 CreatePanel 高级设置一致） */}
          <div>
            <label className={`${labelCls} flex items-center gap-1.5`}>
              <Video className="h-3 w-3" />
              运镜倾向
              <span className={hintCls}>留空 = 保持任务已保存的运镜（或由模型自由分镜）</span>
            </label>
            <div className="grid grid-cols-3 gap-2">
              <select
                value={form.shotSize}
                onChange={(e) => setField('shotSize')(e.target.value)}
                className={selectCls}
              >
                <option value="">景别：自动</option>
                {SHOT_SIZE_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
              <select
                value={form.cameraAngle}
                onChange={(e) => setField('cameraAngle')(e.target.value)}
                className={selectCls}
              >
                <option value="">机位：自动</option>
                {CAMERA_ANGLE_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
              <select
                value={form.cameraMove}
                onChange={(e) => setField('cameraMove')(e.target.value)}
                className={selectCls}
              >
                <option value="">运镜：自动</option>
                {CAMERA_MOVE_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* ④ 时间轴 */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>总时长（秒）</label>
              <input
                type="number"
                min={4}
                max={600}
                value={form.totalSeconds}
                onChange={(e) => setField('totalSeconds')(e.target.value)}
                placeholder="留空 = 不改"
                className={inputCls}
              />
            </div>
            <div>
              <label className={labelCls}>镜头数</label>
              <input
                type="number"
                min={1}
                max={20}
                value={form.shotCount}
                onChange={(e) => setField('shotCount')(e.target.value)}
                placeholder="留空 = 不改"
                className={inputCls}
              />
            </div>
            <p className="col-span-full text-[10px] leading-relaxed text-slate-400">
              两者都填时按「总时长 ÷ 镜头数」分配每镜秒数（单镜 4~12 秒，超出会自动钳制）。
            </p>
          </div>

          {mutation.isError && (
            <p className="text-[11px] text-red-600">
              {mutation.error instanceof Error ? mutation.error.message : '重新生成失败，请重试'}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            disabled={mutation.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={mutation.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-violet-600 to-purple-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:from-violet-700 hover:to-purple-700 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
          >
            {mutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {mutation.isPending ? '提交中…' : '重新生成'}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}
