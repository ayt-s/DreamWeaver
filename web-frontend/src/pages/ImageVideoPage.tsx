import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  AlertTriangle,
  FolderOpen,
  Loader2,
  Sparkles,
  UploadCloud,
  Wand2,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { createVideoTask, listTasks, uploadImage } from '../api/tasks';
import CanvasComposer from '../components/CanvasComposer';
import { useTaskStore } from '../store/taskStore';
import { cachedImageUrl, type CanvasSegment } from '../types/task';

/**
 * 图生视频 — 无限画布独立页。
 *
 * 画布：沿一条线摆放「片段卡」（参考图 + 视频描述 + 时长），
 * 顺序支持拖拽 + 上移/下移按钮调整；模型侧按序逐段生成并拼接成长视频。
 *
 * 素材来源：
 * - 历史作品：已完成任务的图片产物（agnes 平台云 URL，可正常用于生成）
 * - 本地上传：仅用于画布预览；agnes 生成接口要求参考图为公网可访问 URL，
 *   本机 localhost 图片提交生成时会被平台拒绝（400），界面已如实提示。
 */
export default function ImageVideoPage() {
  const navigate = useNavigate();
  const setActiveTask = useTaskStore((s) => s.setActiveTask);
  const [segments, setSegments] = useState<CanvasSegment[]>([]);
  const [sourceTab, setSourceTab] = useState<'history' | 'upload'>('history');
  const [uploaded, setUploaded] = useState<{ url: string; name: string }[]>([]);
  const [globalPrompt, setGlobalPrompt] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 历史作品图（每任务 imageUrls JSON 摊平）
  const { data: page, isLoading: loadingHistory } = useQuery({
    queryKey: ['canvas-history-images'],
    queryFn: () => listTasks({ page: 1, size: 50 }),
  });
  const historyImages: { url: string; taskId: number }[] = [];
  for (const t of page?.list ?? []) {
    if (t.status !== 'completed') continue;
    try {
      const urls: unknown = t.imageUrls ? JSON.parse(t.imageUrls) : [];
      if (Array.isArray(urls)) {
        for (const u of urls) {
          if (typeof u === 'string' && u) historyImages.push({ url: u, taskId: t.id });
        }
      }
    } catch {
      // 忽略解析失败的历史任务
    }
  }

  const mutation = useMutation({
    mutationFn: createVideoTask,
    onSuccess: (task) => {
      setActiveTask(task.id);
      navigate('/');
    },
  });

  const addImageSegment = (url: string) => {
    setSegments((prev) => [...prev, { imageUrl: url, prompt: '', seconds: 5 }]);
  };

  const handleFilePick = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      const res = await uploadImage(file);
      setUploaded((prev) => [...prev, res]);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const submit = () => {
    const valid = segments.filter((s) => s.imageUrl.trim());
    if (valid.length === 0) {
      window.alert('画布中至少需要一个带图片的片段');
      return;
    }
    mutation.mutate({
      prompt:
        globalPrompt.trim() ||
        valid.map((s) => s.prompt.trim()).filter(Boolean).join('；') ||
        '无限画布图生视频',
      genType: 'image_video',
      segments: JSON.stringify(
        valid.map((s) => ({
          image_url: s.imageUrl.trim(),
          prompt: s.prompt.trim(),
          seconds: s.seconds || 5,
        })),
      ),
    });
  };

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      {/* Header */}
      <div className="mb-6 flex items-center gap-3">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100"
        >
          <ArrowLeft className="h-4 w-4" />
          返回创作
        </Link>
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-purple-600">
          <Wand2 className="h-5 w-5 text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-slate-900">
            图生视频 · 无限画布
          </h1>
          <p className="text-xs text-slate-500">
            沿一条线摆放片段卡，每段生成几秒小视频，按顺序拼接成长视频
          </p>
        </div>
      </div>

      {/* 画布 */}
      <section className="mb-6">
        <CanvasComposer segments={segments} onChange={setSegments} size="large" />
      </section>

      {/* 素材来源 */}
      <section className="mb-6 space-y-3">
        <div className="flex items-center gap-2">
          <FolderOpen className="h-4 w-4 text-violet-500" />
          <span className="text-sm font-semibold text-slate-700">添加画布素材</span>
          <span className="text-[10px] text-slate-400">点击图片追加为画布新片段</span>
          <div className="ml-auto flex items-center gap-1 rounded-lg bg-slate-100 p-1">
            {(
              [
                ['history', '历史作品'],
                ['upload', '本地上传'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setSourceTab(key)}
                className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                  sourceTab === key
                    ? 'bg-white text-violet-700 shadow-sm'
                    : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {sourceTab === 'history' ? (
          loadingHistory ? (
            <div className="flex items-center justify-center py-10 text-sm text-slate-400">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              加载历史作品…
            </div>
          ) : historyImages.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 py-8 text-center text-xs text-slate-400">
              还没有可用的历史作品图，先去创作页生成几张文生图
            </div>
          ) : (
            <div className="grid max-h-64 grid-cols-4 gap-3 overflow-y-auto rounded-xl border border-slate-100 p-3 sm:grid-cols-6">
              {historyImages.map((img, i) => (
                <button
                  key={`${img.taskId}-${i}`}
                  type="button"
                  onClick={() => addImageSegment(img.url)}
                  className="group relative overflow-hidden rounded-lg border border-slate-200 bg-slate-100"
                  title={`任务 #${img.taskId} 图片`}
                >
                  <img
                    src={cachedImageUrl(img.url)}
                    alt={`历史作品 ${img.taskId}`}
                    loading="lazy"
                    className="aspect-square w-full object-cover transition-transform group-hover:scale-105"
                    onError={(e) => {
                      (e.target as HTMLImageElement).style.display = 'none';
                    }}
                  />
                </button>
              ))}
            </div>
          )
        ) : (
          <div className="space-y-3 rounded-xl border border-slate-100 p-3">
            <div className="flex items-center gap-2 text-[11px] text-amber-600">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              提示：agnes 生成接口要求参考图为公网可访问 URL，本地上传图仅可画布预览，正式生成需使用历史作品图
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => {
                void handleFilePick(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-violet-300 px-4 py-2 text-xs font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:opacity-50"
            >
              <UploadCloud className="h-4 w-4" />
              {uploading ? '上传中…' : '选择图片上传（jpg/png/webp，≤10MB）'}
            </button>
            {uploaded.length > 0 && (
              <div className="grid max-h-48 grid-cols-4 gap-3 overflow-y-auto sm:grid-cols-6">
                {uploaded.map((img, i) => (
                  <button
                    key={`${img.url}-${i}`}
                    type="button"
                    onClick={() => addImageSegment(img.url)}
                    className="group relative overflow-hidden rounded-lg border border-slate-200 bg-slate-100"
                    title={img.name}
                  >
                    <img
                      src={img.url}
                      alt={img.name}
                      loading="lazy"
                      className="aspect-square w-full object-cover transition-transform group-hover:scale-105"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {/* 全局描述 + 提交 */}
      <section className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-end">
        <div className="flex-1">
          <textarea
            value={globalPrompt}
            onChange={(e) => setGlobalPrompt(e.target.value)}
            placeholder="可选：整条视频的全局描述/风格基调（如：暖色调胶片质感，节奏舒缓）"
            rows={2}
            className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-relaxed focus:border-violet-500 focus:bg-white focus:outline-none"
          />
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={mutation.isPending}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-purple-600 px-8 py-3 text-sm font-medium text-white shadow-md transition-all hover:shadow-lg disabled:from-slate-300 disabled:to-slate-300"
        >
          {mutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              AI 导演工作中…
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" />
              开始创作
            </>
          )}
        </button>
      </section>

      {mutation.isError && (
        <p className="mt-3 text-sm text-red-600">
          {mutation.error instanceof Error ? mutation.error.message : '提交失败，请重试'}
        </p>
      )}
    </main>
  );
}