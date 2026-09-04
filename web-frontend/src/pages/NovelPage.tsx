import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  BookOpen,
  Check,
  Film,
  Loader2,
  Sparkles,
  Wand2,
  X,
} from 'lucide-react';
import { preprocessNovel, toCanvas } from '../api/novel';
import type { NovelProject, NovelSegment } from '../types/novel';

type Phase = 'input' | 'processing' | 'segments' | 'error';

export default function NovelPage() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>('input');
  const [projectName, setProjectName] = useState('');
  const [novelText, setNovelText] = useState('');
  const [targetSegments, setTargetSegments] = useState(6);
  const [secondsPerSegment, setSecondsPerSegment] = useState(5);
  const [project, setProject] = useState<NovelProject | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [converting, setConverting] = useState(false);

  const canSubmit = projectName.trim() && novelText.trim().length >= 100;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setPhase('processing');
    setErrorMsg('');
    try {
      const p = await preprocessNovel({
        projectName: projectName.trim(),
        novelText: novelText.trim(),
        targetSegments,
        secondsPerSegment,
      });
      setProject(p);
      if (p.status === 'ready') {
        setPhase('segments');
      } else {
        setErrorMsg(p.errorMessage || '预处理失败');
        setPhase('error');
      }
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase('error');
    }
  };

  const handleToCanvas = async () => {
    if (!project) return;
    setConverting(true);
    try {
      const canvas = await toCanvas(project.id);
      navigate(`/canvas?project=${canvas.id}`);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase('error');
    } finally {
      setConverting(false);
    }
  };

  const reset = () => {
    setProjectName('');
    setNovelText('');
    setProject(null);
    setErrorMsg('');
    setPhase('input');
  };

  return (
    <div className="flex h-screen flex-col bg-slate-950 text-slate-100">
      {/* 顶栏 */}
      <header className="flex items-center gap-3 border-b border-slate-800 px-4 py-2.5">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs hover:bg-slate-800"
        >
          <ArrowLeft className="h-4 w-4" /> 返回首页
        </Link>
        <div className="flex items-center gap-2">
          <BookOpen className="h-4 w-4 text-indigo-400" />
          <h1 className="text-sm font-semibold">漫剧工厂 · 小说转视频</h1>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <PhasePill phase={phase} />
        </div>
      </header>

      {/* 主内容 */}
      <main className="flex-1 overflow-auto">
        {phase === 'input' && (
          <InputPanel
            projectName={projectName}
            novelText={novelText}
            targetSegments={targetSegments}
            secondsPerSegment={secondsPerSegment}
            canSubmit={!!canSubmit}
            setProjectName={setProjectName}
            setNovelText={setNovelText}
            setTargetSegments={setTargetSegments}
            setSecondsPerSegment={setSecondsPerSegment}
            onSubmit={handleSubmit}
          />
        )}
        {phase === 'processing' && (
          <ProcessingPanel onBack={reset} />
        )}
        {phase === 'segments' && project && (
          <SegmentsPanel
            project={project}
            converting={converting}
            onToCanvas={handleToCanvas}
            onBack={reset}
          />
        )}
        {phase === 'error' && (
          <ErrorPanel errorMsg={errorMsg} project={project} onRetry={handleSubmit} onBack={reset} onBackToSegments={() => project && setPhase('segments')} />
        )}
      </main>
    </div>
  );
}

function PhasePill({ phase }: { phase: Phase }) {
  const map: Record<Phase, { label: string; cls: string }> = {
    input: { label: '① 输入原文', cls: 'border-indigo-500 text-indigo-300 bg-indigo-500/10' },
    processing: { label: '② 预处理中', cls: 'border-amber-500 text-amber-300 bg-amber-500/10' },
    segments: { label: '③ 分镜预览', cls: 'border-emerald-500 text-emerald-300 bg-emerald-500/10' },
    error: { label: '失败', cls: 'border-red-500 text-red-300 bg-red-500/10' },
  };
  const m = map[phase];
  return <span className={`rounded-full border px-2.5 py-0.5 font-medium ${m.cls}`}>{m.label}</span>;
}

// ==================== Tab 1：输入 ====================
function InputPanel(props: {
  projectName: string;
  novelText: string;
  targetSegments: number;
  secondsPerSegment: number;
  canSubmit: boolean;
  setProjectName: (v: string) => void;
  setNovelText: (v: string) => void;
  setTargetSegments: (v: number) => void;
  setSecondsPerSegment: (v: number) => void;
  onSubmit: () => void;
}) {
  const {
    projectName, novelText, targetSegments, secondsPerSegment,
    setProjectName, setNovelText, setTargetSegments, setSecondsPerSegment, onSubmit,
  } = props;

  const charCount = novelText.length;
  const valid = useMemo(() => charCount >= 100 && projectName.trim(), [charCount, projectName]);

  return (
    <div className="mx-auto max-w-4xl space-y-4 p-6">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="mb-4 flex items-center gap-2">
          <BookOpen className="h-5 w-5 text-indigo-400" />
          <h2 className="text-base font-semibold">粘贴小说文本</h2>
          <span className="ml-auto text-[11px] text-slate-500">支持单章或整段，≥ 100 字</span>
        </div>

        <label className="mb-1.5 block text-xs text-slate-400">项目名称 *</label>
        <input
          value={projectName}
          onChange={(e) => setProjectName(e.target.value)}
          placeholder="如：长生烬·第一章"
          maxLength={64}
          className="mb-4 w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-indigo-500"
        />

        <div className="mb-1.5 flex items-center">
          <label className="text-xs text-slate-400">小说正文 *</label>
          <span className={`ml-2 text-[11px] ${charCount >= 100 ? 'text-emerald-400' : 'text-slate-500'}`}>
            {charCount} / 200,000 字
          </span>
        </div>
        <textarea
          value={novelText}
          onChange={(e) => setNovelText(e.target.value)}
          placeholder="将小说原文粘贴到这里…（自动识别章节标记，比如「第一章」「第1章」等）"
          rows={14}
          maxLength={200000}
          className="w-full resize-y rounded-lg border border-slate-700 bg-slate-800 px-3 py-2.5 text-sm leading-relaxed outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
        <div>
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="text-slate-400">目标分镜数</span>
            <span className="font-medium text-indigo-300">{targetSegments}</span>
          </div>
          <input
            type="range"
            min={4}
            max={12}
            value={targetSegments}
            onChange={(e) => setTargetSegments(Number(e.target.value))}
            className="w-full accent-indigo-500"
          />
          <div className="mt-1 text-[10px] text-slate-500">4-12 段，内容不足时会自动少于目标</div>
        </div>
        <div>
          <div className="mb-1.5 flex items-center justify-between text-xs">
            <span className="text-slate-400">每段时长</span>
            <span className="font-medium text-indigo-300">{secondsPerSegment} 秒</span>
          </div>
          <input
            type="range"
            min={4}
            max={12}
            value={secondsPerSegment}
            onChange={(e) => setSecondsPerSegment(Number(e.target.value))}
            className="w-full accent-indigo-500"
          />
          <div className="mt-1 text-[10px] text-slate-500">4-12 秒，对齐 agnes 视频模型支持范围</div>
        </div>
      </div>

      <button
        onClick={onSubmit}
        disabled={!valid}
        className="w-full rounded-xl bg-indigo-600 py-3 text-sm font-semibold hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <span className="inline-flex items-center gap-2">
          <Sparkles className="h-4 w-4" /> 开始预处理（约 30-90 秒）
        </span>
      </button>
      {!valid && (
        <p className="text-center text-[11px] text-slate-500">
          请填写项目名称并粘贴 ≥ 100 字的小说正文
        </p>
      )}
    </div>
  );
}

// ==================== Tab 2：处理中 ====================
function ProcessingPanel({ onBack }: { onBack: () => void }) {
  return (
    <div className="mx-auto max-w-2xl p-8">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-8 text-center">
        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-indigo-500/20">
          <Loader2 className="h-7 w-7 animate-spin text-indigo-400" />
        </div>
        <h2 className="mb-2 text-lg font-semibold">AI 正在分析小说…</h2>
        <p className="mb-6 text-sm text-slate-400">约 30-90 秒，请耐心等待</p>

        <ol className="mx-auto max-w-md space-y-2 text-left text-xs">
          <Step active icon={<span className="text-base">📖</span>}>规则切章</Step>
          <Step active icon={<Sparkles className="h-4 w-4" />}>综合分析（识别角色/场景/视觉风格）</Step>
          <Step active icon={<Film className="h-4 w-4" />}>生成分镜脚本</Step>
          <Step active icon={<Wand2 className="h-4 w-4" />}>拼装生成 prompt（含角色锁定+红线）</Step>
        </ol>

        <button
          onClick={onBack}
          className="mt-6 inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs hover:bg-slate-800"
        >
          <X className="h-3.5 w-3.5" /> 取消
        </button>
      </div>
    </div>
  );
}

function Step({ active, icon, children }: { active: boolean; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2 text-slate-300">
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-500/20 text-indigo-300">
        {icon}
      </span>
      <span>{children}</span>
      {active && <Loader2 className="ml-auto h-3.5 w-3.5 animate-spin text-indigo-400" />}
    </li>
  );
}

// ==================== Tab 3：分镜预览 ====================
function SegmentsPanel(props: {
  project: NovelProject;
  converting: boolean;
  onToCanvas: () => void;
  onBack: () => void;
}) {
  const { project, converting, onToCanvas, onBack } = props;
  const totalSec = project.segments.reduce((s, x) => s + (x.seconds || 0), 0);

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      {/* 概要 */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="flex items-start gap-4">
          <div className="flex-1">
            <div className="mb-1 flex items-center gap-2">
              <Check className="h-4 w-4 text-emerald-400" />
              <h2 className="text-base font-semibold">预处理完成</h2>
            </div>
            <p className="text-sm text-slate-300">{project.projectName}</p>
            {project.visualStyle && (
              <p className="mt-1 text-xs text-indigo-300">视觉风格：{project.visualStyle}</p>
            )}
            <div className="mt-2 flex items-center gap-4 text-xs text-slate-400">
              <span>{project.segments.length} 个分镜</span>
              <span>·</span>
              <span>总时长约 {totalSec} 秒</span>
              <span>·</span>
              <span>章节 {project.chaptersJson ? '已切分' : '未标记'}</span>
            </div>
          </div>
          <button
            onClick={onToCanvas}
            disabled={converting}
            className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-5 py-2.5 text-sm font-semibold hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {converting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Film className="h-4 w-4" />
            )}
            转入无限画布
          </button>
        </div>
      </div>

      {/* 分镜列表 */}
      <div className="space-y-2">
        <div className="flex items-center justify-between px-1">
          <h3 className="text-sm font-medium text-slate-300">分镜预览（可在画布内继续微调）</h3>
          <span className="text-[11px] text-slate-500">编辑请进入画布使用 AI 助手</span>
        </div>
        {project.segments.map((seg, i) => (
          <SegmentCard key={seg.id || i} seg={seg} index={i} />
        ))}
      </div>

      <div className="flex justify-center gap-2">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-4 py-2 text-xs hover:bg-slate-800"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> 返回并新建
        </button>
      </div>
    </div>
  );
}

function SegmentCard({ seg, index }: { seg: NovelSegment; index: number }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="mb-2 flex items-start gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-500/20 text-xs font-semibold text-indigo-300">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <h4 className="text-sm font-semibold text-slate-100">{seg.title}</h4>
            <span className="text-[10px] text-slate-500">第 {seg.chapter} 章 · {seg.seconds}s</span>
          </div>
          <div className="mt-0.5 flex flex-wrap gap-1.5 text-[10px]">
            {seg.characters.map((c) => (
              <span key={c} className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">
                {c}
              </span>
            ))}
            {seg.mood && <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-amber-300">{seg.mood}</span>}
          </div>
        </div>
      </div>

      <div className="mb-2 space-y-1.5 text-xs">
        <Field label="场景" value={seg.scene} />
        <Field label="镜头" value={seg.camera} />
        <Field label="情节" value={seg.plot} />
      </div>

      <details className="group rounded-lg border border-slate-800 bg-slate-950/50">
        <summary className="cursor-pointer select-none px-3 py-1.5 text-[11px] text-slate-400 hover:text-slate-300">
          <span className="group-open:hidden">查看生成 prompt →</span>
          <span className="hidden group-open:inline">↑ 收起 prompt</span>
        </summary>
        <div className="space-y-2 px-3 pb-2">
          <div>
            <div className="mb-1 text-[10px] font-medium text-indigo-400">🖼️ 图片 prompt</div>
            <pre className="whitespace-pre-wrap break-words rounded bg-slate-900 p-2 text-[11px] leading-relaxed text-slate-300">
              {seg.imagePrompt}
            </pre>
          </div>
          <div>
            <div className="mb-1 text-[10px] font-medium text-rose-400">🎬 视频 prompt</div>
            <pre className="whitespace-pre-wrap break-words rounded bg-slate-900 p-2 text-[11px] leading-relaxed text-slate-300">
              {seg.videoPrompt}
            </pre>
          </div>
        </div>
      </details>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | undefined }) {
  if (!value) return null;
  return (
    <div className="flex gap-2">
      <span className="w-10 shrink-0 text-slate-500">{label}</span>
      <span className="text-slate-300">{value}</span>
    </div>
  );
}

// ==================== 错误 ====================
function ErrorPanel(props: {
  errorMsg: string;
  project: NovelProject | null;
  onRetry: () => void;
  onBack: () => void;
  onBackToSegments: () => void;
}) {
  const { errorMsg, project, onRetry, onBack, onBackToSegments } = props;
  return (
    <div className="mx-auto max-w-2xl p-8">
      <div className="rounded-2xl border border-red-900/50 bg-red-950/20 p-6">
        <div className="mb-3 flex items-center gap-2">
          <X className="h-5 w-5 text-red-400" />
          <h2 className="text-base font-semibold text-red-300">预处理失败</h2>
        </div>
        <p className="mb-4 whitespace-pre-wrap break-words rounded-lg bg-red-950/40 p-3 text-xs text-red-200">
          {errorMsg || '未知错误'}
        </p>
        <div className="flex gap-2">
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-xs font-medium hover:bg-red-500"
          >
            <Wand2 className="h-3.5 w-3.5" /> 重试
          </button>
          {project && project.segments && project.segments.length > 0 && (
            <button
              onClick={onBackToSegments}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-4 py-2 text-xs hover:bg-slate-800"
            >
              查看已生成的分镜
            </button>
          )}
          <button
            onClick={onBack}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-4 py-2 text-xs hover:bg-slate-800"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> 返回修改
          </button>
        </div>
      </div>
    </div>
  );
}
