import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Handle,
  Position,
  MarkerType,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  ArrowLeft,
  Upload,
  ImagePlus,
  Type,
  Clapperboard,
  Wand2,
  Loader2,
  Sun,
  Moon,
  Save,
  FolderPlus,
  Trash2,
  Bot,
  Images,
  Plus,
  X,
  RefreshCw,
  Tags,
} from 'lucide-react';
import {
  createVideoTask,
  getTask,
  listTasks,
  uploadImage,
} from '../api/tasks';
import ChatPanel from '../components/ChatPanel';
import {
  createProject,
  listProjects,
  getProject,
  saveProject,
  deleteProject,
  type CanvasProjectView,
  type SaveCanvasResult,
  getCanvasVersion,
} from '../api/canvas';
import { generateText } from '../api/agent';
import { cachedImageUrl, parseImageUrls, type TaskResponse } from '../types/task';
import { reorderShotX, sortShots } from '../utils/shotOrder';
import {
  CAMERA_ANGLE_OPTIONS,
  CAMERA_MOVE_OPTIONS,
  SHOT_SIZE_OPTIONS,
  hasCameraSpec,
  type CameraSpec,
} from '../types/task';

/* ------------------------------------------------------------------ */
/* 工具：从项目名剥离章节标识，得到"小说名"用于下拉分组                    */
/* ------------------------------------------------------------------ */

/** 从项目名剥离章节后缀，得到小说名。
 *  "长生烬-第一章" → "长生烬"
 *  "长生烬 第一章 警花的恐惧" → "长生烬"
 *  "长生烬01" → "长生烬"
 *  剥离失败返回原名（保底）。
 */
function stripChapterSuffix(name: string): string {
  const trimmed = name.trim();
  const chapterPattern =
    /[-·\s]?(?:第\s*[\d一二三四五六七八九十百]+章|[\d一二三四五六七八九十]+)\s*[-·\s]*[^-\d一二三四五六七八九十\s]*$/;
  const stripped = trimmed.replace(chapterPattern, '').trim();
  return stripped || trimmed;
}

/* ------------------------------------------------------------------ */
/* 节点数据模型                                                         */
/* ------------------------------------------------------------------ */

interface TextNodeData {
  content: string;
  mode: string;
}
interface ImageNodeData {
  imageUrl: string;
  prompt: string;
  ratio: string;
  /** 一键文生图产出的多张候选（同 prompt 多次请求）；点缩略图切换 imageUrl */
  candidates?: string[];
  /** 结构化运镜（景别/机位/运镜），可选；空则不注入提示词 */
  cameraSpec?: CameraSpec;
}
interface VideoNodeData {
  seconds: number;
}

type GraphNode = Node<any>;

const RATIO_PRESETS = ['16:9', '9:16', '1:1', '4:3', '3:4'];
/** 归一化 prompt：剥掉 [角色锚]/[镜头] 等方括号块并压缩空白。
 *  回填已生成图时只在**精确匹配失败**才用它 —— 用户随手改个标点，
 *  整段 [角色锚] 就变了，精确匹配会直接失效（图其实还在库里）。 */
const normalizePrompt = (s: string) =>
  (s || '').replace(/\[[^\]]*\]/g, ' ').replace(/\s+/g, ' ').trim();

// 文本节点原来的下拉（一句话生成剧本 / 文生图 / 文生视频 / 图片反推提示词）已移除：
// 四项全是 disabled 的占位（过度设计），而真正缺的「调文本模型补内容」反而没有。
// 现在文本节点 = textarea + AI 生成/改写按钮；data.mode 仅为兼容老画布数据保留。
const VIDEO_MODELS = [
  { value: 'agnes-video-2.5-flash', label: 'Agnes Video 2.5 Flash（快）' },
  { value: 'agnes-video-2.5', label: 'Agnes Video 2.5 HD（慢但清晰）' },
];

/** agnes 只收公网 URL：本地/内网图（上传产物）只能预览，不能用于生成 */
export function isPublicImageUrl(url: string): boolean {
  const u = url.trim().toLowerCase();
  if (u.startsWith('http://localhost') || u.startsWith('http://127.')) return false;
  if (u.startsWith('http://10.') || u.startsWith('http://192.168.')) return false;
  if (/^http:\/\/172\.(1[6-9]|2\d|3[01])\./.test(u)) return false;
  return true;
}

/**
 * 文生图单张：提交 text_image 任务并轮询到完成，返回图片 URL。
 *
 * 抽成模块级函数是为了「一键文生图」批量流程与单节点按钮共用同一套超时/失败语义，
 * 免得两处各写一套轮询逻辑、各自演化。
 */
async function generateOneImage(
  prompt: string,
  count = 1,
  timeoutMs = 180_000,
): Promise<string[]> {
  const res = await createVideoTask({
    prompt,
    genType: 'text_image',
    // 直出图：跳过 agent 侧需求解析/剧本/分镜，一次出 count 张同 prompt 候选
    directImage: true,
    imageCount: count,
    // 素材标记：画廊默认不展示（否则一次批量会在草稿区刷出 N 个任务）
    source: 'canvas_asset',
  });
  const taskId = Number(res.id);
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    await new Promise((r) => setTimeout(r, 4000));
    const cur: TaskResponse | null = await getTask(taskId);
    if (!cur) throw new Error('任务查询失败');
    if (cur.status === 'completed') {
      const urls = parseImageUrls(cur.imageUrls);
      if (urls.length > 0) return urls;
      throw new Error('生成完成但无图片');
    }
    if (cur.status === 'failed' || cur.status === 'expired') {
      throw new Error(cur.errorMessage || '生成失败');
    }
    if (cur.status === 'interrupted') {
      throw new Error('生成已中断（Agent 可能正在恢复，稍后可重试）');
    }
  }
  throw new Error('文生图超时（120s）');
}

const CANVAS_THEME_KEY = 'dreamweaver:canvas-theme';

const selectCls =
  'rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-700 outline-none focus:border-indigo-300';
const textareaCls =
  'w-full resize-none rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700 outline-none focus:border-indigo-300';

/* ------------------------------------------------------------------ */
/* 自定义节点组件（模块级定义，React Flow 要求 nodeTypes 静态稳定）        */
/* ------------------------------------------------------------------ */

/**
 * 节点右上角的删除按钮（hover 显示）。
 *
 * 用 React Flow v12 的 `deleteElements`：它会把该节点的关联连线一并删掉，
 * 比手写 setNodes/setEdges 过滤干净。
 * `nodrag` class 必须加 —— 否则点它会被 React Flow 当成拖拽起点，按钮点不动。
 */
function NodeDeleteButton({ id }: { id: string }) {
  const { deleteElements } = useReactFlow();
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        void deleteElements({ nodes: [{ id }] });
      }}
      title="删除该节点（关联连线一并删除）"
      className="nodrag absolute -right-2 -top-2 z-10 hidden rounded-full border border-slate-200 bg-white p-1 text-slate-400 shadow-sm transition hover:border-red-300 hover:text-red-500 group-hover:block"
    >
      <X className="h-3 w-3" />
    </button>
  );
}


function TextNodeView({ id, data }: NodeProps<GraphNode>) {
  const { updateNodeData } = useReactFlow();
  const [aiInput, setAiInput] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState('');
  const patch = (p: Partial<TextNodeData>) => updateNodeData(id, p);
  const content = (data.content || '').trim();

  /** 让文本模型生成/改写节点内容：有输入按输入来，没输入就把现有内容改写成画面提示词 */
  const runAi = async () => {
    const intent = aiInput.trim();
    if (!intent && !content) {
      setAiError('先写点内容，或填一句「想要什么」');
      return;
    }
    setAiLoading(true);
    setAiError('');
    try {
      const text = await generateText(intent, content);
      if (text) {
        patch({ content: text });
        setAiInput('');
      } else {
        setAiError('模型返回空内容');
      }
    } catch (e) {
      setAiError(e instanceof Error ? e.message : 'AI 生成失败');
    } finally {
      setAiLoading(false);
    }
  };
  return (
    <div className="group relative w-60 rounded-xl border border-slate-200 bg-white p-3 shadow-md">
      <NodeDeleteButton id={id} />
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !bg-indigo-400" />
      <div className="mb-2 flex items-center gap-1 text-[11px] font-semibold text-slate-500">
        <Type className="h-3.5 w-3.5" /> 文本节点
      </div>
      <textarea
        value={data.content}
        onChange={(e) => patch({ content: e.target.value })}
        rows={4}
        placeholder="描述画面内容，或输入一句提示词…"
        className={textareaCls}
      />
      {/* AI 生成/改写：把意图交给文本模型（单轮，不走画布助手），结果直接落进节点内容 */}
      <div className="mt-2 flex items-center gap-1">
        <input
          value={aiInput}
          onChange={(e) => setAiInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void runAi();
            }
          }}
          placeholder={content ? '怎么改？（留空=改写为画面提示词）' : '想要什么画面？'}
          className="nodrag min-w-0 flex-1 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-700 outline-none focus:border-indigo-300"
        />
        <button
          type="button"
          onClick={() => void runAi()}
          disabled={aiLoading}
          title="让文本模型按你的要求生成/改写这段内容（100~200 字画面提示词）"
          className="nodrag inline-flex shrink-0 items-center gap-1 rounded-lg border border-indigo-200 px-2 py-1 text-[11px] font-medium text-indigo-600 transition hover:bg-indigo-50 disabled:opacity-50"
        >
          {aiLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wand2 className="h-3 w-3" />}
          AI
        </button>
      </div>
      {aiError && <div className="mt-1 text-[10px] text-red-500">{aiError}</div>}
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !bg-indigo-400" />
    </div>
  );
}

function ImageNodeView({ id, data }: NodeProps<GraphNode>) {
  const { updateNodeData, getNodes, setNodes } = useReactFlow();
  const fileRef = useRef<HTMLInputElement>(null);
  const [generating, setGenerating] = useState(false);
  const [status, setStatus] = useState('');
  const patch = (p: Partial<ImageNodeData>) => updateNodeData(id, p);

  // === 分镜顺序（上移 / 下移）===
  // 成片顺序 = 图片节点**从左到右的 x 坐标**（唯一判据；后端 agent 的 reorder_shots
  // 用的是同一条规则）。拖动节点确实能改顺序，但要把 6~10 个节点拖到互相精确的前后
  // 位置很难（间距不齐时尤其），所以给「第 N 段」徽标配了 ▲▼。
  // 排序/换位的规则抽在 utils/shotOrder.ts（纯函数 + 单测，因为排错了不会报错，
  // 只会让成片顺序不对）。
  const ORDER = (data as { __order?: number }).__order ?? 0;
  const ORDER_TOTAL = (data as { __orderTotal?: number }).__orderTotal ?? 0;

  const moveShot = (dir: -1 | 1) => {
    const shots = sortShots(
      getNodes()
        .filter((n) => n.type === 'imageNode')
        .map((n) => ({ id: n.id, x: n.position.x, y: n.position.y })),
    );
    const xById = reorderShotX(shots, shots.findIndex((s) => s.id === id), dir);
    if (!xById) return; // 已经是第一段/最后一段
    setNodes((nds) =>
      nds.map((n) => {
        const x = xById.get(n.id);
        return x === undefined ? n : { ...n, position: { ...n.position, x } };
      }),
    );
  };

  const onUploadFile = async (file: File) => {
    try {
      const res = await uploadImage(file);
      patch({ imageUrl: res.url });
      setStatus('已更换素材');
    } catch (e) {
      setStatus(e instanceof Error ? e.message : '上传失败');
    }
  };

  // 文生图：以本节点 prompt 为提示词生成图片，完成后自动填参考图
  const startTextToImage = async () => {
    const prompt = (data.prompt || '').trim();
    if (!prompt) {
      setStatus('请先填写提示词');
      return;
    }
    setGenerating(true);
    setStatus('文生图进行中…');
    try {
      const res = await createVideoTask({
        prompt,
        genType: 'text_image',
        // ⚠️ 必须走直出短路（与「一键文生图」一致）：不传 directImage 时 Java 不加
        // `direct_image`，agent 会**按 prompt 重新拆镜** → 一次白出 3~5 张不同画面的图，
        // 而这里只用得上第 1 张（实测过的额度浪费，见 TaskServiceImpl.java:380-383）。
        directImage: true,
      });
      const taskId = Number(res.id);
      const t0 = Date.now();
      while (Date.now() - t0 < 90_000) {
        await new Promise((r) => setTimeout(r, 4000));
        const cur: TaskResponse | null = await getTask(taskId);
        if (!cur) break;
        if (cur.status === 'completed') {
          const urls = parseImageUrls(cur.imageUrls);
          if (urls.length > 0) {
            patch({ imageUrl: urls[0] });
            setStatus('已生成参考图');
          } else {
            setStatus('生成完成但无图片');
          }
          break;
        }
        if (cur.status === 'failed' || cur.status === 'expired') {
          setStatus('生成失败：' + (cur.errorMessage || '未知原因'));
          break;
        }
        // 已中断：任务已停下（后端可能稍后自动续跑），不要一直卡在「进行中…」
        if (cur.status === 'interrupted') {
          setStatus('任务已中断，Agent 将在后台自动恢复续跑，可稍后刷新查看');
          break;
        }
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : '文生图失败');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="group relative w-64 rounded-xl border border-indigo-200 bg-white p-3 shadow-md">
      <NodeDeleteButton id={id} />
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !bg-indigo-400" />
      <div className="mb-2 flex items-center gap-1 text-[11px] font-semibold text-slate-500">
        <ImagePlus className="h-3.5 w-3.5" /> 图片节点
        {/* 成片顺序徽标 + 前后移动：chain 按 x 坐标排，拖动节点也能改顺序 —— 不显示序号用户看不出来 */}
        {ORDER > 0 ? (
          <span className="ml-auto flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              className="nodrag rounded px-0.5 text-[10px] text-indigo-500 hover:bg-indigo-50 disabled:opacity-25"
              disabled={ORDER <= 1}
              title="前移一段（在成片里提前）"
              onClick={() => moveShot(-1)}
            >
              ▲
            </button>
            <span
              className="rounded-full bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600"
              title="成片里的第几段（按画布从左到右排序）"
            >
              第 {ORDER} 段
            </span>
            <button
              type="button"
              className="nodrag rounded px-0.5 text-[10px] text-indigo-500 hover:bg-indigo-50 disabled:opacity-25"
              disabled={ORDER_TOTAL === 0 || ORDER >= ORDER_TOTAL}
              title="后移一段（在成片里推后）"
              onClick={() => moveShot(1)}
            >
              ▼
            </button>
          </span>
        ) : null}
      </div>

      {/* 图片预览 / 占位：有图显示图；无图但有 prompt 显示 prompt 预览（小说转画布常用）；都没有显示默认占位 */}
      <div className="relative mb-2 flex h-36 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
        {data.imageUrl ? (
          <img
            src={cachedImageUrl(data.imageUrl)}
            alt="参考图"
            className="h-full w-full object-contain"
          />
        ) : data.prompt && data.prompt.trim() ? (
          <div className="flex flex-col items-center justify-center gap-1.5 px-3 text-center">
            <Wand2 className="h-5 w-5 text-indigo-300" />
            <div className="text-[10px] font-medium text-indigo-500">
              待生成 · 点「文生图」
            </div>
            <p className="line-clamp-4 max-w-full text-[10px] leading-relaxed text-slate-600">
              {data.prompt}
            </p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-1 text-slate-400">
            <Wand2 className="h-8 w-8" />
            <span className="text-[11px]">填提示词 → 一键文生图</span>
          </div>
        )}
      </div>
      {data.candidates && data.candidates.length > 1 && (
        <div className="mb-2">
          <div className="mb-1 text-[10px] text-slate-500">
            候选 {data.candidates.length} 张 · 点一张设为首帧
          </div>
          <div className="flex gap-1 overflow-x-auto pb-0.5">
            {(data.candidates as string[]).map((u: string, i: number) => (
              <button
                key={`${id}-cand-${i}`}
                type="button"
                onClick={() => patch({ imageUrl: u })}
                title={`候选 ${i + 1}（点击作为该镜首帧）`}
                className={`h-11 w-11 shrink-0 overflow-hidden rounded border transition ${
                  data.imageUrl === u
                    ? 'border-indigo-500 ring-1 ring-indigo-400'
                    : 'border-slate-200 hover:border-indigo-300'
                }`}
              >
                <img
                  src={cachedImageUrl(u)}
                  alt={`候选 ${i + 1}`}
                  className="h-full w-full object-cover"
                />
              </button>
            ))}
          </div>
        </div>
      )}
      {data.imageUrl && !isPublicImageUrl(data.imageUrl) && (
        <div className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
          本地上传图仅可预览，生成需公网图：请用历史作品或点「文生图」生成
        </div>
      )}

      <div className="mb-2 flex items-center gap-1.5">
        <span className="shrink-0 text-[11px] text-slate-500">比例</span>
        <select
          value={data.ratio}
          onChange={(e) => patch({ ratio: e.target.value })}
          className={selectCls + ' w-full'}
        >
          {RATIO_PRESETS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>

      {/* ③ 结构化运镜：景别 / 机位 / 运镜（留空=不指定，交给模型） */}
      <div className="mb-2 space-y-1">
        <div className="flex items-center gap-1">
          <span className="w-8 shrink-0 text-[11px] text-slate-500">景别</span>
          <select
            value={data.cameraSpec?.shot_size ?? ''}
            onChange={(e) => patch({ cameraSpec: { ...data.cameraSpec, shot_size: e.target.value } })}
            className={selectCls + ' w-full'}
          >
            <option value="">不指定</option>
            {SHOT_SIZE_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-8 shrink-0 text-[11px] text-slate-500">机位</span>
          <select
            value={data.cameraSpec?.angle ?? ''}
            onChange={(e) => patch({ cameraSpec: { ...data.cameraSpec, angle: e.target.value } })}
            className={selectCls + ' w-full'}
          >
            <option value="">不指定</option>
            {CAMERA_ANGLE_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-1">
          <span className="w-8 shrink-0 text-[11px] text-slate-500">运镜</span>
          <select
            value={data.cameraSpec?.movement ?? ''}
            onChange={(e) => patch({ cameraSpec: { ...data.cameraSpec, movement: e.target.value } })}
            className={selectCls + ' w-full'}
          >
            <option value="">不指定</option>
            {CAMERA_MOVE_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
      </div>

      <textarea
        value={data.prompt}
        onChange={(e) => patch({ prompt: e.target.value })}
        rows={2}
        placeholder="本段描述（留空则用上游文本节点内容）"
        className={textareaCls + ' mb-2'}
      />

      <div className="flex items-center gap-1.5">
        <button
          onClick={() => fileRef.current?.click()}
          className="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-600 hover:bg-slate-100"
        >
          <Upload className="h-3.5 w-3.5" /> 替换
        </button>
        <button
          onClick={startTextToImage}
          disabled={generating}
          className="inline-flex flex-1 items-center justify-center gap-1 rounded-lg bg-indigo-600 px-2 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
        >
          {generating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Wand2 className="h-3.5 w-3.5" />
          )}
          文生图
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onUploadFile(f);
            e.target.value = '';
          }}
        />
      </div>
      {status && <div className="mt-1.5 text-[11px] text-slate-500">{status}</div>}

      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !bg-indigo-400" />
    </div>
  );
}

function VideoNodeView({ id, data }: NodeProps<GraphNode>) {
  const { updateNodeData } = useReactFlow();
  return (
    <div className="group relative w-52 rounded-xl border-2 border-indigo-500 bg-white p-3 shadow-md">
      <NodeDeleteButton id={id} />
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !bg-indigo-500" />
      <div className="mb-2 flex items-center gap-1 text-[11px] font-semibold text-indigo-600">
        <Clapperboard className="h-3.5 w-3.5" /> 成片 · 长视频合成
      </div>
      <div className="flex items-center gap-1.5">
        <span className="shrink-0 text-[11px] text-slate-500">每段时长</span>
        <select
          value={data.seconds}
          onChange={(e) => updateNodeData(id, { seconds: Number(e.target.value) })}
          className={selectCls + ' w-full'}
        >
          {[3, 4, 5, 6].map((s) => (
            <option key={s} value={s}>
              {s} 秒
            </option>
          ))}
        </select>
      </div>
      <div className="mt-2 text-[11px] text-slate-400">
        由上游图片节点逐段生成，自动拼接
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 主画布页面                                                           */
/* ------------------------------------------------------------------ */

/** 生成不与现有节点冲突的 id：取现有 `n<数字>` 的最大编号 +1。
 *  原先用模块级自增计数器（从 n1 开始），与 initialNodes 的硬编码 id（n1..n3）撞车：
 *  点「添加节点」生成 n1/n2/n3 会**覆盖同 id 的老节点**，把成片节点悄悄换成文本节点 ——
 *  表现出来就是"点了添加没反应，反而少了个节点"。改成看现有节点取最大值+1，稳态不会重号。 */
const nextFreeNodeId = (list: GraphNode[]) => {
  let max = 0;
  for (const n of list) {
    const m = /^n(\d+)$/.exec(n.id);
    if (m) max = Math.max(max, Number(m[1]));
  }
  return `n${max + 1}`;
};

const initialNodes: GraphNode[] = [
  {
    id: 'n1',
    type: 'textNode',
    position: { x: 40, y: 120 },
    data: { content: '雪山日出，金色晨光洒满峰顶，云雾缓缓流动', mode: '自己编写' },
  },
  {
    id: 'n2',
    type: 'imageNode',
    position: { x: 380, y: 120 },
    data: { imageUrl: '', prompt: '', ratio: '16:9' },
  },
  {
    id: 'n3',
    type: 'videoNode',
    position: { x: 720, y: 120 },
    data: { seconds: 4 },
  },
];
const initialEdges: Edge[] = [
  {
    id: 'e1',
    source: 'n1',
    target: 'n2',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#818cf8' },
  },
  {
    id: 'e2',
    source: 'n2',
    target: 'n3',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#818cf8' },
  },
];

const nodeTypes = { textNode: TextNodeView, imageNode: ImageNodeView, videoNode: VideoNodeView };

export default function CanvasPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState<GraphNode>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [videoModel, setVideoModel] = useState(VIDEO_MODELS[0].value);
  // 可灵式精细控制（画布全局）：风格 + 负面词，提交时透传给 agent
  const [stylePrompt, setStylePrompt] = useState('');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [controlPanelOpen, setControlPanelOpen] = useState(false);
  // 元素语义绑定：名词 → 参考图编号（<Picture N>），key = 锚定图标识，value = 剧本中的名词
  const [bindingNames, setBindingNames] = useState<Record<string, string>>({});
  const [bindingPanelOpen, setBindingPanelOpen] = useState(false);
  // 背景偏好持久化：localStorage 即时保存，另随项目画布数据一起保存（跨设备）
  // 默认白底（light）；用户手动切过再按 localStorage 走
  const [dark, setDark] = useState<boolean>(
    () => localStorage.getItem(CANVAS_THEME_KEY) === 'dark',
  );
  useEffect(() => {
    localStorage.setItem(CANVAS_THEME_KEY, dark ? 'dark' : 'light');
  }, [dark]);
  const [chatPanelOpen, setChatPanelOpen] = useState(false);
  const [projects, setProjects] = useState<CanvasProjectView[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<number | null>(null);
  const [projectName, setProjectName] = useState('');
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  // URL ?anchorRefs 携带从小说转画布时生成的角色/场景锚定图
  const [searchParams, setSearchParams] = useSearchParams();

  // 乐观锁：本地持有的画布版本号。保存时回传；版本不符 = 画布已被别处修改（多标签/多设备），
  // 后端不写入并回传服务端现状，由用户决定保留哪一份。
  // ⚠️ 每次保存成功都必须把它更新为返回值，否则下一次保存必然误报「冲突」。
  const versionRef = useRef(0);
  const anchorRefsParam = searchParams.get('anchorRefs');
  const anchorRefs = useMemo(() => {
    if (!anchorRefsParam) return null;
    try {
      const parsed = JSON.parse(decodeURIComponent(anchorRefsParam));
      if (parsed && typeof parsed === 'object') return parsed as { characters?: Record<string, string>; scenes?: Record<string, string> };
      return null;
    } catch {
      return null;
    }
  }, [anchorRefsParam]);

  // 锚定图面板 state（角色/场景锚定图，key 是名称，value 是 URL）
  const [anchorPanelOpen, setAnchorPanelOpen] = useState(false);
  const [anchorCharRefs, setAnchorCharRefs] = useState<Record<string, string>>({});
  const [anchorSceneRefs, setAnchorSceneRefs] = useState<Record<string, string>>({});
  const [charRefName, setCharRefName] = useState('');
  const [charRefUrl, setCharRefUrl] = useState('');
  const [sceneRefName, setSceneRefName] = useState('');
  const [sceneRefUrl, setSceneRefUrl] = useState('');
  const [regenerating, setRegenerating] = useState<{ kind: 'char' | 'scene'; name: string } | null>(null);
  const anchorRefBox = useRef<HTMLDivElement>(null);

  // 元素语义绑定（④）：参考图编号必须与提交时的组装顺序严格一致，
  // 否则会绑错对象。组装规则（见 mutation）：
  //   每段 reference_images = [本段图, ...角色锚定图, ...场景锚定图]，截断 5 张
  // 因此 Picture 1 = 每段自己的图（逐段不同，不可全局绑定），Picture 2 起才是锚定图。
  // 锚定图来源与提交保持一致：优先画布 state，URL anchorRefs 兜底。
  const effectiveCharRefs =
    Object.keys(anchorCharRefs).length > 0 ? anchorCharRefs : (anchorRefs?.characters ?? {});
  const effectiveSceneRefs =
    Object.keys(anchorSceneRefs).length > 0 ? anchorSceneRefs : (anchorRefs?.scenes ?? {});
  // agnes reference 模式硬限制 5 张图（与 canvas_storyboarder_node 的截断一致）
  const MAX_REF_PICTURES = 5;
  const bindingRows = useMemo(() => {
    const rows: { key: string; label: string; url: string; pictureIndex: number }[] = [];
    let idx = 2; // Picture 1 是每段自己的图，锚定图从 2 开始
    for (const [name, url] of Object.entries(effectiveCharRefs)) {
      rows.push({ key: `char:${name}`, label: name, url, pictureIndex: idx++ });
    }
    for (const [name, url] of Object.entries(effectiveSceneRefs)) {
      rows.push({ key: `scene:${name}`, label: name, url, pictureIndex: idx++ });
    }
    return rows;
  }, [effectiveCharRefs, effectiveSceneRefs]);
  // 生效的绑定行（未超出 5 张上限 + 名词非空）
  const activeBindingRows = bindingRows.filter(
    (r) => r.pictureIndex <= MAX_REF_PICTURES && (bindingNames[r.key] ?? r.label).trim(),
  );

  // 切换项目时，从项目数据同步锚定图 state
  useEffect(() => {
    if (!currentProjectId) {
      setAnchorCharRefs({});
      setAnchorSceneRefs({});
      return;
    }
    const p = projects.find((x) => x.id === currentProjectId);
    if (!p) return;
    const parseJson = (s?: string | null) => {
      if (!s) return {};
      try {
        const o = JSON.parse(s);
        return o && typeof o === 'object' ? (o as Record<string, string>) : {};
      } catch {
        return {};
      }
    };
    setAnchorCharRefs(parseJson(p.characterRefs));
    setAnchorSceneRefs(parseJson(p.sceneRefs));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProjectId, projects]);

  // URL ?anchorRefs=<base64> 自动合并到当前画布的锚定图 state
  // 优先级：URL anchorRefs > 画布现有 anchorRefs
  // 必须在项目加载完成后执行，否则 currentProjectId 还没设置
  useEffect(() => {
    if (!anchorRefs || !currentProjectId) return;
    const chars = anchorRefs.characters ?? {};
    const scenes = anchorRefs.scenes ?? {};
    // 合并到现有 state（覆盖同名 key）
    setAnchorCharRefs((prev) => ({ ...prev, ...chars }));
    setAnchorSceneRefs((prev) => ({ ...prev, ...scenes }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorRefs, currentProjectId]);

  // 点击面板外部关闭
  useEffect(() => {
    if (!anchorPanelOpen) return;
    const onClick = (e: MouseEvent) => {
      // e.target 类型是 EventTarget，需要 cast；用 any 避开 @xyflow 的 Node 类型遮蔽
      if (anchorRefBox.current && !anchorRefBox.current.contains(e.target as any)) {
        setAnchorPanelOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [anchorPanelOpen]);

  // 保存锚定图到当前项目（无项目先新建）
  const saveAnchorsToProject = async (charRefs: Record<string, string>, sceneRefs: Record<string, string>) => {
    let id = currentProjectId;
    const isNewProject = id === null;
    if (id === null) {
      const name = projectName.trim() || `画布 ${new Date().toLocaleTimeString()}`;
      const p = await createProject(name);
      id = p.id;
      setProjects((ps) => [...ps, p]);
      setCurrentProjectId(p.id);
      setProjectName(p.name);
    }
    const charJson = Object.keys(charRefs).length > 0 ? JSON.stringify(charRefs) : undefined;
    const sceneJson = Object.keys(sceneRefs).length > 0 ? JSON.stringify(sceneRefs) : undefined;
    const res = await saveProject(id, {
      characterRefs: charJson,
      sceneRefs: sceneJson,
      // 新建项目版本为 0；已存在项目用本地版本，避免把别人的并发改动静默覆盖
      version: isNewProject ? 0 : versionRef.current,
    });
    if (res.conflict) {
      window.alert('画布已在别处被修改，锚定图未保存。请刷新页面后重试。');
      return;
    }
    versionRef.current = res.canvas?.version ?? versionRef.current + 1;
    // 更新本地 projects 缓存，让面板切换项目时能看到
    setProjects((ps) => ps.map((x) => (x.id === id ? { ...x, characterRefs: charJson, sceneRefs: sceneJson } : x)));
  };

  // 添加角色锚定图
  const addCharRef = async () => {
    const name = charRefName.trim();
    const url = charRefUrl.trim();
    if (!name || !url) return;
    if (!/^https?:\/\//.test(url)) {
      window.alert('URL 必须是 http:// 或 https:// 开头');
      return;
    }
    const next = { ...anchorCharRefs, [name]: url };
    setAnchorCharRefs(next);
    setCharRefName('');
    setCharRefUrl('');
    try {
      await saveAnchorsToProject(next, anchorSceneRefs);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '保存失败');
    }
  };

  // 删除角色锚定图
  const removeCharRef = async (name: string) => {
    const next = { ...anchorCharRefs };
    delete next[name];
    setAnchorCharRefs(next);
    try {
      await saveAnchorsToProject(next, anchorSceneRefs);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '保存失败');
    }
  };

  // 添加场景锚定图
  const addSceneRef = async () => {
    const name = sceneRefName.trim();
    const url = sceneRefUrl.trim();
    if (!name || !url) return;
    if (!/^https?:\/\//.test(url)) {
      window.alert('URL 必须是 http:// 或 https:// 开头');
      return;
    }
    const next = { ...anchorSceneRefs, [name]: url };
    setAnchorSceneRefs(next);
    setSceneRefName('');
    setSceneRefUrl('');
    try {
      await saveAnchorsToProject(anchorCharRefs, next);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '保存失败');
    }
  };

  // 重新生成单个锚定图：prompt 弹框填描述（默认当前名称），调 agent anchors 生成新 URL 覆盖
  const regenerateAnchor = async (kind: 'char' | 'scene', name: string) => {
    const defaultDesc = name;
    const descInput = window.prompt(
      `重新生成「${name}」的锚定图。请输入角色/场景描述（用于生成提示词）：`,
      defaultDesc,
    );
    if (descInput === null) return; // 取消
    const description = descInput.trim() || defaultDesc;
    setRegenerating({ kind, name });
    try {
      const res = await fetch('/v1/novel/anchors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          kind === 'char'
            ? { characters: { [name]: description } }  // characters 是 dict{name: desc}
            : { scenes: [description] },               // scenes 是 string[]
        ),
      });
      const json = await res.json();
      // agent 直接返回 NovelAnchorsResponse：{code, message, data: {characters: {name: url}, scenes: {desc: url}}}
      // scenes 返回 key 是 desc（描述文本），不是 name，所以场景用 description 作 key 取
      const newUrl = kind === 'char'
        ? json?.data?.characters?.[name]
        : json?.data?.scenes?.[description];
      if (!newUrl) {
        window.alert(`重新生成失败：${json?.message || '未知错误'}`);
        return;
      }
      if (kind === 'char') {
        const next = { ...anchorCharRefs, [name]: newUrl };
        setAnchorCharRefs(next);
        await saveAnchorsToProject(next, anchorSceneRefs);
      } else {
        const next = { ...anchorSceneRefs, [name]: newUrl };
        setAnchorSceneRefs(next);
        await saveAnchorsToProject(anchorCharRefs, next);
      }
    } catch (e) {
      window.alert(e instanceof Error ? `重新生成失败：${e.message}` : '重新生成失败');
    } finally {
      setRegenerating(null);
    }
  };

  // 删除场景锚定图
  const removeSceneRef = async (name: string) => {
    const next = { ...anchorSceneRefs };
    delete next[name];
    setAnchorSceneRefs(next);
    try {
      await saveAnchorsToProject(anchorCharRefs, next);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '保存失败');
    }
  };

  // 载入项目列表
  useEffect(() => {
    listProjects()
      .then((ps) => setProjects(ps))
      .catch(() => setProjects([]));
  }, []);

  // URL ?project=<id> 自动加载该项目（从 NovelPage 转画布跳转时带上）
  // 必须在项目列表加载完成后执行，否则 onSelectProject 内部 getProject 虽能直接调后端，
  // 但下拉框选中态需要 projects 列表里也有这一项。用 setTimeout 延后一拍。
  const projectParam = searchParams.get('project');
  useEffect(() => {
    if (projectParam && !currentProjectId) {
      const id = Number(projectParam);
      if (Number.isFinite(id) && id > 0) {
        // 延后一拍，让项目列表先加载完（下拉框选中态更准）
        setTimeout(() => onSelectProject(id), 50);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 序列化：只保留画布持久化所需字段
  const serializeCanvas = useCallback(() => {
    const edgesJson = JSON.stringify(
      edges.map(({ id, source, target, markerEnd }) => {
        const e: Record<string, unknown> = { id, source, target };
        if (markerEnd) e.markerEnd = markerEnd;
        return e;
      }),
    );
    // theme 随画布数据持久化：nodesJson 用 {theme, nodes} 包装（老数据是纯数组，加载时兼容）
    return {
      // 不把主题写进 nodesJson：黑/白底是画布的 UI 偏好（走 localStorage，全局一致），
      // 混进画布数据会导致「切换项目就换主题」，也污染了本该只描述画布的内容。
      nodesJson: JSON.stringify({ nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })) }),
      edgesJson,
    };
  }, [nodes, edges, dark]);

  // 删除当前项目：确认后服务端删除，本地清空为新建态
  const onDeleteProject = async () => {
    if (!currentProjectId) return;
    // 别把"项目名"寄托在本地列表上：从小说页直开(?project=N)或列表还没加载完时，
    // projects 里可能根本没有这一项 —— 原来的 `if (!p) return` 会让点删除毫无反应。
    const p = projects.find((x) => x.id === currentProjectId);
    const label = p?.name ?? projectName ?? `#${currentProjectId}`;
    if (!window.confirm(`删除项目「${label}」？删除后画布内容不可恢复。`)) return;
    try {
      await deleteProject(currentProjectId);
      setProjects((ps) => ps.filter((x) => x.id !== currentProjectId));
      setCurrentProjectId(null);
      setProjectName('');
      setNodes(initialNodes);
      setEdges([]);
      // 清掉 URL 上的 ?project=：否则刷新会回来加载一个刚被删掉的项目
      setSearchParams({}, { replace: true });
      window.alert('已删除');
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '删除失败');
    }
  };

  // 新建项目：先 prompt 新名，再 prompt 选择已有项目（可跳过）作为起点
  // 选了就 clone（复用源项目的 nodes/edges），否则空白画布
  const onCreateProject = async () => {
    const name = window.prompt('新建画布项目名称：');
    if (!name || !name.trim()) return;
    try {
      const p = await createProject(name.trim());
      // 让用户选一个已有项目作为起点（可跳过）
      let cloneFrom: number | null = null;
      if (projects.length > 0) {
        const listText = projects
          .map((x) => `${x.id}. ${x.name}`)
          .join('\n');
        const input = window.prompt(
          `可选：选择一个已有项目作为起点（clone 其画布）。\n\n` +
            `${listText}\n\n输入项目 ID 数字，或输入 0 / 留空 / 点取消 = 空白画布：`,
          '',
        );
        if (input && input.trim() !== '0') {
          const id = Number(input.trim());
          if (Number.isInteger(id) && projects.some((x) => x.id === id)) {
            cloneFrom = id;
          }
        }
      }
      if (cloneFrom !== null) {
        const src = await getProject(cloneFrom);
        if (src.nodesJson || src.edgesJson) {
          // clone 成功：保存源内容到新项目
          await saveProject(p.id, {
            nodesJson: src.nodesJson || undefined,
            edgesJson: src.edgesJson || undefined,
          });
          // 加载源内容到本地
          onSelectProject(p.id);
          setProjects((ps) => ps.map((x) => (x.id === p.id ? { ...x, nodesJson: src.nodesJson, edgesJson: src.edgesJson } : x)));
          window.alert(`已基于「${src.name}」创建「${p.name}」`);
        } else {
          window.alert(`「${src.name}」为空画布，已创建空白新项目「${p.name}」`);
          setNodes(initialNodes);
          setEdges(initialEdges);
        }
      } else {
        setNodes(initialNodes);
        setEdges(initialEdges);
      }
      setProjects((ps) => [...ps, p]);
      setCurrentProjectId(p.id);
      setProjectName(p.name);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '创建失败');
    }
  };

  // 删除节点时同步清掉关联连线（键盘 Delete/Backspace 删除也走这里）
  const onNodesDelete = useCallback(
    (deleted: GraphNode[]) => {
      const ids = new Set(deleted.map((n) => n.id));
      setEdges((es) => es.filter((e) => !ids.has(e.source) && !ids.has(e.target)));
    },
    [setEdges],
  );

  // 保存当前画布到当前项目（无项目先新建）
  const onSaveCanvas = async () => {
    const { nodesJson, edgesJson } = serializeCanvas();
    try {
      let id = currentProjectId;
      const isNewProject = id === null;
      if (id === null) {
        const name = projectName.trim() || `画布 ${new Date().toLocaleTimeString()}`;
        const p = await createProject(name);
        id = p.id;
        setCurrentProjectId(p.id);
        setProjectName(p.name);
        setProjects((ps) => [...ps, p]);
      }
      const res = await saveProject(id, {
        name: projectName.trim() || undefined,
        nodesJson,
        edgesJson,
        version: isNewProject ? 0 : versionRef.current,
      });
      if (res.conflict) {
        resolveConflict(res, nodesJson, edgesJson);
        return;
      }
      versionRef.current = res.canvas?.version ?? versionRef.current + 1;
      lastSavedSnapshotRef.current = `${nodesJson}|${edgesJson}`;
      setProjects((ps) =>
        ps.map((p) => (p.id === id ? { ...p, name: projectName.trim() || p.name } : p)),
      );
      window.alert('已保存');
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '保存失败');
    }
  };

  // 切换到指定项目：加载其节点/连线
  const onSelectProject = async (id: number) => {
    if (id === currentProjectId) return;
    try {
      const p = await getProject(id);
      if (p.nodesJson) {
        const parsed = JSON.parse(p.nodesJson);
        const ns = Array.isArray(parsed) ? parsed : (parsed.nodes ?? []);
        setNodes(ns);
        setEdges(JSON.parse(p.edgesJson ?? '[]'));
        // 主题不再从项目里读：黑/白底是全局 UI 偏好（localStorage 唯一来源）。
        // 老项目 nodesJson 里可能残留 theme 字段——忽略即可，下次保存自动消失。
      } else {
        setNodes(initialNodes);
        setEdges(initialEdges);
        // 空项目也不强制切主题：保留用户当前的全局偏好
      }
      setCurrentProjectId(p.id);
      setProjectName(p.name);
      versionRef.current = p.version ?? 0;   // 乐观锁基线：本页基于这一版编辑
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '加载失败');
    }
  };

  const onConnect = useCallback(
    (conn: Connection) =>
      setEdges((eds) =>
        addEdge({ ...conn, markerEnd: { type: MarkerType.ArrowClosed, color: '#818cf8' } }, eds),
      ),
    [setEdges],
  );

  // 主链串行化：沿连线方向收集节点。
  // 顺序规则：imageNode 按画布 x 坐标升序排列（用户拖节点左右 = 调整片段先后顺序）。
  // 每个 img 后跟着它出边的 vid → compose（vid 的入度是 1，出边唯一到 compose）。
  // 未连线的孤立 img 也按 x 坐标纳入。
  // 这是"按画布顺序"的核心——用户看到的画布布局 = 提交时的拼接顺序。
  const chain = useMemo(() => {
    const outgoing = new Map<string, string>();
    for (const e of edges) {
      outgoing.set(e.source, e.target);
    }
    // 收集所有 imageNode，按 x 坐标升序（画布上"从左到右" = 片段先后顺序）
    const imageNodes = nodes
      .filter((n) => n.type === 'imageNode')
      .sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
    const order: string[] = [];
    const seen = new Set<string>();
    for (const imgNode of imageNodes) {
      let cur: string | undefined = imgNode.id;
      while (cur && !seen.has(cur)) {
        seen.add(cur);
        order.push(cur);
        cur = outgoing.get(cur);
      }
    }
    return order;
  }, [nodes, edges]);

  // 生成计划：段列表 + 文本 + 每段时长
  const plan = useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.id, n] as const));
    const segments: Array<{
      image_url: string;
      prompt: string;
      seconds: number;
      aspect_ratio: string;
      camera_spec?: CameraSpec;
    }> = [];
    const texts: string[] = [];
    let videoSeconds = 4;
    for (const id of chain) {
      const node = byId.get(id);
      if (!node) continue;
      if (node.type === 'videoNode') {
        videoSeconds = (node.data as VideoNodeData).seconds || 4;
      } else if (node.type === 'textNode') {
        const c = (node.data as TextNodeData).content.trim();
        if (c) texts.push(c);
      } else if (node.type === 'imageNode') {
        const img = node.data as ImageNodeData;
        if (!img.imageUrl.trim()) continue;
        const incoming = edges.find((e) => e.target === id);
        const textN = incoming ? byId.get(incoming.source) : undefined;
        const prompt =
          textN && textN.type === 'textNode'
            ? (textN.data as TextNodeData).content.trim()
            : img.prompt.trim();
        segments.push({
          image_url: img.imageUrl.trim(),
          prompt,
          seconds: videoSeconds,
          aspect_ratio: img.ratio || '16:9',
          // 结构化运镜：空 spec 不写字段，避免给 agent 塞无意义空对象
          ...(hasCameraSpec(img.cameraSpec) ? { camera_spec: img.cameraSpec } : {}),
        });
      }
    }
    return { segments, texts, videoSeconds };
  }, [chain, nodes, edges]);

  const addNode = useCallback(
    (type: GraphNode['type']) => {
      setNodes((nds) => {
        const offset = nds.length * 40;
        const base: GraphNode = { id: nextFreeNodeId(nds), type, position: { x: 60 + offset, y: 360 + offset }, data: {} as never };
        if (type === 'textNode') {
          base.data = { content: '', mode: '自己编写' };
        } else if (type === 'imageNode') {
          base.data = { imageUrl: '', prompt: '', ratio: '16:9' };
        } else {
          base.data = { seconds: 4 };
        }
        return [...nds, base];
      });
    },
    [setNodes],
  );

  const addImageNode = useCallback(
    (url: string) => {
      setNodes((nds) => [
        ...nds,
        {
          id: nextFreeNodeId(nds),
          type: 'imageNode',
          position: { x: 380, y: 420 + nds.length * 40 },
          data: { imageUrl: url, prompt: '', ratio: '16:9' },
        },
      ]);
    },
    [setNodes],
  );

  const onUploadAsset = async (file: File) => {
    try {
      const res = await uploadImage(file);
      addImageNode(res.url);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '上传失败');
    }
  };

  // 素材来源：历史作品（文生图成品，展示缓存图）
  const { data: history } = useQuery({
    queryKey: ['canvas-history-images'],
    queryFn: async () => {
      // includeAssets：画布的一键文生图素材（source=canvas_asset）也要能选回来
      const { list } = await listTasks({
        page: 1,
        size: 40,
        genType: 'text_image',
        includeAssets: true,
      });
      return list.filter((t) => t.status === 'completed');
    },
    staleTime: 30_000,
  });

  const mutation = useMutation({
    mutationFn: () => {
      // 提交前：把画布 state 的锚定图（优先）或 URL anchorRefs（兜底）合并到每个 segment 的 reference_images
      let segmentsJson: string | undefined;
      if (plan.segments.length > 0) {
        // 优先用画布 state（用户手动管理/从 URL 合并过），URL anchorRefs 作兜底
        // （与 bindingRows 用同一来源，保证 <Picture N> 编号对齐）
        const charRefsMap = effectiveCharRefs;
        const sceneRefsMap = effectiveSceneRefs;
        const enriched = plan.segments.map((seg) => {
          const extraRefs: string[] = [];
          for (const url of Object.values(charRefsMap)) extraRefs.push(url);
          for (const url of Object.values(sceneRefsMap)) extraRefs.push(url);
          const merged = [seg.image_url, ...extraRefs].filter((u): u is string => !!u);
          return { ...seg, reference_images: merged.slice(0, MAX_REF_PICTURES) };
        });
        segmentsJson = JSON.stringify(enriched);
      }
      // ④ 元素语义绑定：名词 → <Picture N>（agent 转成占位符，保证角色/道具跨镜一致）
      const referenceBindings =
        activeBindingRows.length > 0
          ? JSON.stringify(
              activeBindingRows.map((r) => ({
                name: (bindingNames[r.key] ?? r.label).trim(),
                imageIndex: r.pictureIndex,
              })),
            )
          : undefined;
      return createVideoTask({
        prompt:
          plan.texts.join('；') ||
          (plan.segments.length > 0 ? '无限画布图生视频' : '无限画布'),
        genType: plan.segments.length > 0 ? 'image_video' : 'text_video',
        segments: segmentsJson,
        videoModel: videoModel || undefined,
        // 可灵式精细控制：全局风格 + 负面词（折进每镜提示词正文）
        stylePrompt: stylePrompt.trim() || undefined,
        negativePrompt: negativePrompt.trim() || undefined,
        // ④ 元素语义绑定：名词 → <Picture N>
        referenceBindings,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tasks'] });
      navigate('/');
    },
  });

  // === 一键文生图：把所有「有提示词但还没有图」的图片节点依次生成首帧 ===
  // 串行、不并发：agnes 图片侧有间隔限制，前端打满只会换来一串 429/队列满。
  // 单张失败不中断整批（部分成功），进度与失败数实时显示在按钮上。
  // 注意：批次由前端驱动，页面关掉就停——服务端化（可离开页面、跨设备可见）留到 P2。
  const pendingImageNodes = useMemo(
    () =>
      nodes.filter((n) => {
        if (n.type !== 'imageNode') return false;
        const d = n.data as ImageNodeData;
        return !(d.imageUrl || '').trim() && (d.prompt || '').trim().length > 0;
      }),
    [nodes],
  );
  // 每个节点生成几张候选（按张计费，3 张是「够挑又不浪费」的默认）
  const [candidateCount, setCandidateCount] = useState(3);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchDone, setBatchDone] = useState(0);
  const [batchTotal, setBatchTotal] = useState(0);
  const [batchFailed, setBatchFailed] = useState(0);
  const batchStopRef = useRef(false);

  const runBatchTextToImage = async () => {
    const targets = pendingImageNodes.map((n) => ({
      id: n.id,
      prompt: ((n.data as ImageNodeData).prompt || '').trim(),
    }));
    if (targets.length === 0) return;
    batchStopRef.current = false;
    setBatchRunning(true);
    setBatchDone(0);
    setBatchFailed(0);
    setBatchTotal(targets.length);
    let failed = 0;
    for (let i = 0; i < targets.length; i++) {
      if (batchStopRef.current) break;
      const t = targets[i];
      try {
        const urls = await generateOneImage(t.prompt, candidateCount);
        setNodes((nds) =>
          nds.map((n) =>
            n.id === t.id
              ? { ...n, data: { ...n.data, candidates: urls, imageUrl: urls[0] } }
              : n,
          ),
        );
      } catch (e) {
        failed += 1;
        setBatchFailed(failed);
        console.warn('批量文生图失败:', t.id, e);
      }
      setBatchDone(i + 1);
    }
    setBatchRunning(false);
  };

  // === 一键回填：从历史素材任务里把已生成的图找回节点 ===
  // 场景：之前生成过、但画布没保存（刷新/重启/换浏览器）→ 节点显示「待生成」，
  // 而图其实还在（素材任务 source=canvas_asset 保留在库里，agnes URL 仍有效）。
  // 按 prompt 精确匹配即可——节点 prompt 是唯一的，不会张冠李戴。
  const [backfillCount, setBackfillCount] = useState<number | null>(null);
  const backfillMutation = useMutation({
    mutationFn: async () => {
      const { list } = await listTasks({
        page: 1,
        size: 50,
        genType: 'text_image',
        includeAssets: true,
      });
      // 两级索引：精确键优先；归一化键**仅在不冲突时**可用（歧义宁可不填——
      // 把 A 镜的图填到 B 镜，用户会直接拿去生成视频，比不填更糟）
      const exact = new Map<string, string[]>();
      const loose = new Map<string, string[]>();
      for (const t of list) {
        if (t.status !== 'completed') continue;
        const key = (t.prompt || '').trim();
        const urls = parseImageUrls(t.imageUrls);
        if (!key || urls.length === 0) continue;
        if (!exact.has(key)) exact.set(key, urls);
        const nk = normalizePrompt(key);
        if (nk) loose.set(nk, loose.has(nk) ? [] : urls);
      }
      let filled = 0;
      setNodes((nds) =>
        nds.map((n) => {
          if (n.type !== 'imageNode') return n;
          const d = n.data as ImageNodeData;
          if ((d.imageUrl || '').trim()) return n;
          const key = (d.prompt || '').trim();
          const urls = exact.get(key) ?? loose.get(normalizePrompt(key));
          if (!urls || urls.length === 0) return n;
          filled += 1;
          return { ...n, data: { ...n.data, candidates: urls, imageUrl: urls[0] } };
        }),
      );
      return filled;
    },
    onSuccess: (filled) => setBackfillCount(filled),
  });

  // === 自动保存：节点/连线有实质变化就防抖写回项目（1.2s） ===
  // 不自动保存的后果：一键文生图回填的图片只活在内存里，刷新/服务重启后节点又变回「待生成」。
  // 用「上次快照」比对，避免刚加载完项目就白写一次。
  const lastSavedSnapshotRef = useRef('');

  /** 自动保存撞版本时的非阻塞横幅（助手也会写画布，阻塞 confirm 会高频打断编辑） */
  const [conflictBanner, setConflictBanner] = useState<
    { res: SaveCanvasResult; nodesJson: string; edgesJson: string; localVersion: number } | null
  >(null);

  // 轻量版本轮询：只取版本号（不拉 JSON）。发现服务端版本更高 = 助手或在另一个标签页
  // 改了这张画布 → 顶部提示可重载。刻意不自动套用：那会打断正在编辑的你。
  const { data: versionProbe } = useQuery({
    queryKey: ['canvas-version', currentProjectId],
    queryFn: () => getCanvasVersion(currentProjectId as number),
    enabled: currentProjectId !== null,
    refetchInterval: 5000,
  });
  const externalVersion =
    versionProbe && versionProbe.version !== undefined && versionProbe.version > versionRef.current
      ? versionProbe.version
      : null;

  /** 载入服务端最新内容（提示条「重载最新」/ 放弃本地改动时用） */
  const reloadFromServer = useCallback(async () => {
    if (currentProjectId === null) return;
    const p = await getProject(currentProjectId);
    const parsed = JSON.parse(p.nodesJson ?? '[]');
    setNodes(Array.isArray(parsed) ? parsed : (parsed.nodes ?? []));
    setEdges(JSON.parse(p.edgesJson ?? '[]'));
    versionRef.current = p.version ?? 0;
    lastSavedSnapshotRef.current = `${p.nodesJson ?? ''}|${p.edgesJson ?? ''}`;
  }, [currentProjectId, setNodes, setEdges]);

  /** 版本冲突处理：二选一（用我的覆盖 / 放弃我的改动）。自动保存与手动保存共用。 */
  /** 用本地内容覆盖服务端：以服务端版本为基线重发，一次即可成功 */
  const applyLocalOverServer = useCallback(
    (res: SaveCanvasResult, nodesJson: string, edgesJson: string) => {
      if (currentProjectId === null) return;
      versionRef.current = res.serverVersion ?? versionRef.current;
      saveProject(currentProjectId, { nodesJson, edgesJson, version: versionRef.current })
        .then((r2) => {
          if (r2.conflict) {
            window.alert('仍然冲突，请刷新页面后重试');
            return;
          }
          versionRef.current = r2.canvas?.version ?? versionRef.current + 1;
          lastSavedSnapshotRef.current = `${nodesJson}|${edgesJson}`;
          window.alert('已用你本地的内容覆盖服务端');
        })
        .catch((e) => window.alert(e instanceof Error ? e.message : '保存失败'));
    },
    [currentProjectId],
  );

  /** 放弃本地改动：直接用冲突响应里带回的服务端内容重载，省一次 GET */
  const loadServerContent = useCallback(
    (res: SaveCanvasResult) => {
      const serverNodesJson = res.serverNodesJson ?? '';
      versionRef.current = res.serverVersion ?? 0;
      if (serverNodesJson) {
        const parsed = JSON.parse(serverNodesJson);
        setNodes(Array.isArray(parsed) ? parsed : (parsed.nodes ?? []));
      }
      setEdges(JSON.parse(res.serverEdgesJson ?? '[]'));
      lastSavedSnapshotRef.current = `${serverNodesJson}|${res.serverEdgesJson ?? ''}`;
      window.alert('已载入服务端最新内容（本地改动已放弃）');
    },
    [setNodes, setEdges],
  );

  /** 手动保存撞版本：这里让用户明确选一次（自动保存走横幅，见上） */
  const resolveConflict = useCallback(
    (res: SaveCanvasResult, nodesJson: string, edgesJson: string) => {
      const useMine = window.confirm(
        `画布已在别处被修改（服务端版本 ${res.serverVersion ?? '?'}，你手上基于版本 ${versionRef.current}）。\n\n` +
          `确定 = 用你当前画布上的内容覆盖服务端\n` +
          `取消 = 放弃你本地的改动，载入服务端最新内容`,
      );
      if (useMine) {
        applyLocalOverServer(res, nodesJson, edgesJson);
        return;
      }
      loadServerContent(res);
    },
    [applyLocalOverServer, loadServerContent],
  );

  useEffect(() => {
    if (currentProjectId === null) return;
    const { nodesJson, edgesJson } = serializeCanvas();
    const key = `${nodesJson}|${edgesJson}`;
    if (lastSavedSnapshotRef.current === '') {
      lastSavedSnapshotRef.current = key; // 首轮 = 刚加载完项目，记快照不写库
      return;
    }
    if (lastSavedSnapshotRef.current === key) return;
    const timer = setTimeout(() => {
      saveProject(currentProjectId, { nodesJson, edgesJson, version: versionRef.current })
        .then((res) => {
          if (res.conflict) {
            // 自动保存撞版本：不弹阻塞对话框（助手现在也会写画布，弹窗会高频打断），
            // 改为顶部横幅，由你决定「用我的覆盖」还是「载入最新」
            setConflictBanner({ res, nodesJson, edgesJson, localVersion: versionRef.current });
            return;
          }
          versionRef.current = res.canvas?.version ?? versionRef.current + 1;
          lastSavedSnapshotRef.current = key;
        })
        .catch(() => {
          /* 静默：网络类失败不打断操作，顶栏「保存」仍可兜底 */
        });
    }, 1200);
    return () => clearTimeout(timer);
  }, [nodes, edges, currentProjectId, serializeCanvas, resolveConflict]);

  // === 成片顺序写进图片节点（画布上显示「第 N 段」）===
  // chain 按 x 坐标排序 → 拖动节点即改顺序；不显示序号用户根本判断不出提交顺序。
  useEffect(() => {
    const order = new Map<string, number>();
    let seq = 0;
    for (const id of chain) {
      const node = nodes.find((x) => x.id === id);
      if (node?.type === 'imageNode') order.set(id, (seq += 1));
    }
    if (order.size === 0) return;
    // __orderTotal 同时写：节点上的 ▲▼ 要用它判断「已经是最后一段」
    const total = order.size;
    setNodes((nds) => {
      let changed = false;
      const next = nds.map((node) => {
        if (node.type !== 'imageNode') return node;
        const want = order.get(node.id) ?? 0;
        const d = node.data as { __order?: number; __orderTotal?: number };
        if (d.__order === want && d.__orderTotal === total) return node;
        changed = true;
        return { ...node, data: { ...node.data, __order: want, __orderTotal: total } };
      });
      return changed ? next : nds;
    });
  }, [chain, nodes, setNodes]);

  const canSubmit = plan.segments.length > 0 || plan.texts.length > 0;
  const totalSeconds = plan.segments.length * plan.videoSeconds;

  // 深/浅色主题样式映射
  const theme = dark
    ? {
        page: 'bg-slate-950',
        header: 'border-slate-800 bg-slate-950',
        headText: 'text-slate-100',
        hint: 'text-slate-500',
        border: 'border-slate-800',
        btn: 'border-slate-700 text-slate-200 hover:bg-slate-800',
        btnBg: 'bg-slate-800',
        label: 'text-slate-500',
        dots: '#1e293b',
        bar: 'border-slate-700 bg-slate-900/90',
        input: 'border-slate-700 bg-slate-800 text-slate-200',
      }
    : {
        page: 'bg-slate-100',
        header: 'border-slate-300 bg-white',
        headText: 'text-slate-900',
        hint: 'text-slate-500',
        border: 'border-slate-300',
        btn: 'border-slate-400 text-slate-700 hover:bg-slate-200',
        btnBg: 'bg-white',
        label: 'text-slate-500',
        dots: '#cbd5e1',
        bar: 'border-slate-300 bg-white/95',
        input: 'border-slate-300 bg-white text-slate-700',
      };

  return (
    <div className={`relative flex h-screen flex-col ${theme.page}`}>
      {/* 顶栏 */}
      <header className={`flex items-center gap-3 border-b ${theme.header} px-4 py-2.5`}>
        <Link
          to="/"
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium ${theme.btn}`}
          title="回到首页创作"
        >
          <ArrowLeft className="h-4 w-4" /> 返回首页
        </Link>
        <Link
          to="/gallery"
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium ${theme.btn}`}
          title="作品画廊"
        >
          <Clapperboard className="h-4 w-4" /> 画廊
        </Link>
        <h1 className={`text-sm font-semibold ${theme.headText}`}>无限画布 · 图生视频</h1>
        <span className={`ml-2 hidden text-[11px] ${theme.hint} xl:inline`}>
          拖拽节点自由摆放 · 滚轮缩放 · 空白处平移 · 连线决定生成顺序
        </span>

        <div className="ml-auto flex items-center gap-2">
          <span className={`text-[11px] ${theme.hint}`}>
            {plan.segments.length > 0
              ? `${plan.segments.length} 段 · 每段 ${plan.videoSeconds}s · 约 ${totalSeconds}s`
              : '未接入图片，将按文本生成视频'}
          </span>
          <div className={`h-5 w-px ${dark ? 'bg-slate-700' : 'bg-slate-300'}`} />
          {/* 项目选择：按小说名分组（<optgroup>），同本小说多章节归到同一组下 */}
          <select
            value={currentProjectId ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              if (v) onSelectProject(Number(v));
            }}
            title="切换画布项目（按小说名分组）"
            className={`rounded-lg border px-2 py-1 text-xs outline-none ${theme.input}`}
          >
            <option value="" disabled>
              {currentProjectId ? '切换项目…' : '新建后保存即成为项目'}
            </option>
            {(() => {
              // 按 stripChapterSuffix 分组：小说名 → [项目]
              const groups = new Map<string, typeof projects>();
              for (const p of projects) {
                const key = stripChapterSuffix(p.name);
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key)!.push(p);
              }
              // 每组内按 id 升序（创建时间顺序），组间按小说名排序
              return Array.from(groups.entries())
                .sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
                .map(([groupName, ps]) => {
                  ps.sort((a, b) => a.id - b.id);
                  return (
                    <optgroup key={groupName} label={groupName}>
                      {ps.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </optgroup>
                  );
                });
            })()}
          </select>
          {/* 项目名称（重命名/新建保存用） */}
          <input
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder="项目名称"
            className={`w-32 rounded-lg border px-2 py-1 text-xs outline-none ${theme.input}`}
          />
          <button
            onClick={onSaveCanvas}
            title="保存画布（尚无项目则会自动新建）"
            className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn}`}
          >
            <Save className="h-3.5 w-3.5" /> 保存
          </button>
          <button
            onClick={onCreateProject}
            title="新建画布项目"
            className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn}`}
          >
            <FolderPlus className="h-3.5 w-3.5" /> 新建
          </button>
          <button
            onClick={onDeleteProject}
            title={currentProjectId ? '删除当前画布项目' : '请先切换到要删除的项目'}
            disabled={!currentProjectId}
            className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn} disabled:cursor-not-allowed disabled:opacity-40`}
          >
            <Trash2 className="h-3.5 w-3.5" /> 删除项目
          </button>
          {/* 背景深/浅切换 */}
          <button
            onClick={() => setDark((d) => !d)}
            title={dark ? '切到白色背景' : '切到黑色背景'}
            className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn}`}
          >
            {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            {dark ? '白底' : '黑底'}
          </button>
          {/* 锚定图管理：角色/场景锚定图，跨章节复用 */}
          <button
            onClick={() => setAnchorPanelOpen((o) => !o)}
            title="管理角色/场景锚定图（跨章节复用）"
            className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn} ${
              Object.keys(anchorCharRefs).length > 0 || Object.keys(anchorSceneRefs).length > 0
                ? 'border-blue-400 text-blue-600'
                : ''
            }`}
          >
            <Images className="h-3.5 w-3.5" /> 锚定图
            {(Object.keys(anchorCharRefs).length > 0 || Object.keys(anchorSceneRefs).length > 0) && (
              <span className="text-[10px] text-blue-500">
                {Object.keys(anchorCharRefs).length + Object.keys(anchorSceneRefs).length}
              </span>
            )}
          </button>
          {/* AI 助手 */}
          <button
            onClick={() => setChatPanelOpen(true)}
            title="打开画布助手（agent）"
            className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-indigo-500"
          >
            <Bot className="h-3.5 w-3.5" /> AI 助手
          </button>
        </div>
      </header>

      {/* 锚定图面板：角色/场景锚定图，跨小说章节复用 */}
      {anchorPanelOpen && (
        <div
          ref={anchorRefBox}
          className={`absolute top-14 right-3 z-40 w-80 rounded-xl border shadow-xl ${dark ? 'bg-slate-800 border-slate-700' : 'bg-white border-slate-200'}`}
        >
          <div className={`flex items-center justify-between border-b px-3 py-2 ${dark ? 'border-slate-700' : 'border-slate-200'}`}>
            <span className={`text-xs font-semibold ${dark ? 'text-white' : 'text-slate-800'}`}>锚定图</span>
            <button
              onClick={() => setAnchorPanelOpen(false)}
              className={`text-xs ${dark ? 'text-slate-400 hover:text-white' : 'text-slate-500 hover:text-slate-800'}`}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto p-3">
            <div className="mb-3">
              <div className={`mb-1.5 text-[11px] font-semibold uppercase tracking-wide ${dark ? 'text-slate-400' : 'text-slate-500'}`}>
                角色锚定图
              </div>
              <div className="flex gap-1">
                <input
                  value={charRefName}
                  onChange={(e) => setCharRefName(e.target.value)}
                  placeholder="名称"
                  className={`w-16 rounded border px-2 py-1 text-xs outline-none ${theme.input}`}
                />
                <input
                  value={charRefUrl}
                  onChange={(e) => setCharRefUrl(e.target.value)}
                  placeholder="图片 URL"
                  className={`flex-1 rounded border px-2 py-1 text-xs outline-none ${theme.input}`}
                />
                <button
                  onClick={addCharRef}
                  disabled={!charRefName.trim() || !charRefUrl.trim()}
                  className={`rounded border px-2 py-1 ${dark ? 'border-slate-600 hover:bg-slate-700' : 'border-slate-300 hover:bg-slate-100'} disabled:cursor-not-allowed disabled:opacity-40`}
                >
                  <Plus className="h-4 w-4" />
                </button>
              </div>
              {Object.entries(anchorCharRefs).map(([name, url]) => (
                <div key={name} className={`mt-2 flex items-center gap-2 rounded border p-1.5 ${dark ? 'border-slate-700 bg-slate-900/40' : 'border-slate-200 bg-slate-50'}`}>
                  <img src={cachedImageUrl(url)} alt={name} className="h-10 w-10 rounded object-cover" />
                  <div className="flex-1 truncate text-xs">{name}</div>
                  <button
                    onClick={() => regenerateAnchor('char', name)}
                    title="重新生成（不满意时替换）"
                    disabled={regenerating?.kind === 'char' && regenerating?.name === name}
                    className={`text-xs ${dark ? 'text-slate-400 hover:text-blue-400' : 'text-slate-500 hover:text-blue-500'} disabled:cursor-wait disabled:opacity-40`}
                  >
                    <RefreshCw className={`h-4 w-4 ${regenerating?.kind === 'char' && regenerating?.name === name ? 'animate-spin' : ''}`} />
                  </button>
                  <button
                    onClick={() => removeCharRef(name)}
                    className={dark ? 'text-xs text-slate-400 hover:text-red-400' : 'text-xs text-slate-500 hover:text-red-500'}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
              {Object.keys(anchorCharRefs).length === 0 && (
                <div className={`mt-2 text-xs ${dark ? 'text-slate-500' : 'text-slate-400'}`}>暂无角色锚定图</div>
              )}
            </div>
            <div className="border-t pt-3" style={{ borderColor: dark ? '#334155' : '#e2e8f0' }}>
              <div className={`mb-1.5 text-[11px] font-semibold uppercase tracking-wide ${dark ? 'text-slate-400' : 'text-slate-500'}`}>
                场景锚定图
              </div>
              <div className="flex gap-1">
                <input
                  value={sceneRefName}
                  onChange={(e) => setSceneRefName(e.target.value)}
                  placeholder="名称"
                  className={`w-16 rounded border px-2 py-1 text-xs outline-none ${theme.input}`}
                />
                <input
                  value={sceneRefUrl}
                  onChange={(e) => setSceneRefUrl(e.target.value)}
                  placeholder="图片 URL"
                  className={`flex-1 rounded border px-2 py-1 text-xs outline-none ${theme.input}`}
                />
                <button
                  onClick={addSceneRef}
                  disabled={!sceneRefName.trim() || !sceneRefUrl.trim()}
                  className={`rounded border px-2 py-1 ${dark ? 'border-slate-600 hover:bg-slate-700' : 'border-slate-300 hover:bg-slate-100'} disabled:cursor-not-allowed disabled:opacity-40`}
                >
                  <Plus className="h-4 w-4" />
                </button>
              </div>
              {Object.entries(anchorSceneRefs).map(([name, url]) => (
                <div key={name} className={`mt-2 flex items-center gap-2 rounded border p-1.5 ${dark ? 'border-slate-700 bg-slate-900/40' : 'border-slate-200 bg-slate-50'}`}>
                  <img src={cachedImageUrl(url)} alt={name} className="h-10 w-10 rounded object-cover" />
                  <div className="flex-1 truncate text-xs">{name}</div>
                  <button
                    onClick={() => regenerateAnchor('scene', name)}
                    title="重新生成（不满意时替换）"
                    disabled={regenerating?.kind === 'scene' && regenerating?.name === name}
                    className={`text-xs ${dark ? 'text-slate-400 hover:text-blue-400' : 'text-slate-500 hover:text-blue-500'} disabled:cursor-wait disabled:opacity-40`}
                  >
                    <RefreshCw className={`h-4 w-4 ${regenerating?.kind === 'scene' && regenerating?.name === name ? 'animate-spin' : ''}`} />
                  </button>
                  <button
                    onClick={() => removeSceneRef(name)}
                    className={dark ? 'text-xs text-slate-400 hover:text-red-400' : 'text-xs text-slate-500 hover:text-red-500'}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ))}
              {Object.keys(anchorSceneRefs).length === 0 && (
                <div className={`mt-2 text-xs ${dark ? 'text-slate-500' : 'text-slate-400'}`}>暂无场景锚定图</div>
              )}
            </div>
            <div className={`mt-3 border-t pt-2 text-[11px] ${dark ? 'border-slate-700 text-slate-500' : 'border-slate-200 text-slate-400'}`}>
              锚定图会在提交成片时合并到每个分镜的 reference_images，让角色/场景跨章节保持一致。
            </div>
          </div>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* 左侧面板 */}
        <aside className={`flex w-60 shrink-0 flex-col gap-4 overflow-y-auto border-r ${theme.border} p-3`}>
          <section>
            <div className={`mb-2 text-[11px] font-semibold uppercase tracking-wide ${theme.label}`}>
              添加节点
            </div>
            <div className="flex flex-col gap-1.5">
              <button
                onClick={() => addNode('textNode')}
                className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${theme.btn}`}
              >
                <Type className="h-4 w-4 text-indigo-400" /> 文本节点
              </button>
              <button
                onClick={() => addNode('imageNode')}
                className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${theme.btn}`}
              >
                <ImagePlus className="h-4 w-4 text-indigo-400" /> 图片节点
              </button>
              <button
                onClick={() => addNode('videoNode')}
                className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${theme.btn}`}
              >
                <Clapperboard className="h-4 w-4 text-indigo-400" /> 成片节点
              </button>
            </div>
          </section>

          <section>
            <div className={`mb-2 text-[11px] font-semibold uppercase tracking-wide ${theme.label}`}>
              添加资源
            </div>
            <button
              onClick={() => document.getElementById('canvas-upload')?.click()}
              className={`mb-2 inline-flex w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-xs ${theme.btn}`}
            >
              <Upload className="h-4 w-4 text-indigo-400" /> 本地上传
            </button>
            <input
              id="canvas-upload"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onUploadAsset(f);
                e.target.value = '';
              }}
            />
            <div className={`mb-2 text-[11px] ${theme.label}`}>从历史作品选取（点击入画布）：</div>
            <div className="grid grid-cols-3 gap-1.5">
              {(history ?? []).slice(0, 12).map((t) => {
                const urls = parseImageUrls(t.imageUrls);
                if (urls.length === 0) return null;
                return (
                  <button
                    key={t.id}
                    title={'点击加入画布：' + (t.prompt || `#${t.id}`)}
                    onClick={() => addImageNode(urls[0])}
                    className="group relative aspect-square overflow-hidden rounded-md border border-slate-400 hover:border-indigo-400"
                  >
                    <img
                      src={cachedImageUrl(urls[0])}
                      alt={t.prompt || `任务 ${t.id}`}
                      className="h-full w-full object-cover"
                      loading="lazy"
                    />
                  </button>
                );
              })}
              {(history ?? []).length === 0 && (
                <div className="col-span-3 py-4 text-center text-[11px] text-slate-600">
                  暂无历史作品，先上传或文生图生成
                </div>
              )}
            </div>
          </section>
        </aside>

        {/* 画布 */}
        <main className="relative min-w-0 flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodesDelete={onNodesDelete}
            // 键盘删除节点：Windows 用户习惯 Delete，Backspace 是 React Flow 默认值
            deleteKeyCode={['Delete', 'Backspace']}
            nodeTypes={nodeTypes}
            // React Flow 自带的控件（缩放/适配）跟随全局主题；
            // 不传的话它默认 colorMode='light'，黑底画布上仍是白色控件。
            colorMode={dark ? 'dark' : 'light'}
            fitView
            minZoom={0.15}
            maxZoom={2.5}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={24} size={1.5} color={theme.dots} />
            <Controls className="react-flow__controls" />
          </ReactFlow>

          {/* 底部悬浮控制栏 */}
          <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center px-4">
            <div className="flex flex-col items-center gap-2">
              {/* 精细控制面板：全局风格 + 负面词（可灵式，折进每镜提示词） */}
              {controlPanelOpen && (
                <div className={`pointer-events-auto w-[520px] max-w-[90vw] rounded-2xl border ${theme.bar} p-4 shadow-lg backdrop-blur`}>
                  <div className={`mb-2 flex items-center gap-1.5 text-[11px] font-semibold ${theme.headText}`}>
                    <Wand2 className="h-3.5 w-3.5" />
                    精细控制
                    <span className={`font-normal ${theme.hint}`}>风格 · 负面词（对所有片段生效）</span>
                  </div>
                  <div className="space-y-2">
                    <div>
                      <label className={`mb-1 block text-[10px] ${theme.hint}`}>风格提示词</label>
                      <textarea
                        rows={2}
                        value={stylePrompt}
                        onChange={(e) => setStylePrompt(e.target.value)}
                        placeholder="如：3D写实国漫风，虚幻5，OC渲染，电影级光影，体积光雾"
                        className={`w-full rounded-lg border p-2 text-[11px] leading-relaxed outline-none ${theme.input}`}
                      />
                    </div>
                    <div>
                      <label className={`mb-1 block text-[10px] ${theme.hint}`}>负面提示词</label>
                      <textarea
                        rows={2}
                        value={negativePrompt}
                        onChange={(e) => setNegativePrompt(e.target.value)}
                        placeholder="如：手指畸形、面部崩坏、穿模、画面抖动、水印logo"
                        className={`w-full rounded-lg border p-2 text-[11px] leading-relaxed outline-none ${theme.input}`}
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* 元素绑定面板：名词 → 参考图编号（可灵式 <Picture N>，保证角色/道具跨镜一致） */}
              {bindingPanelOpen && (
                <div className={`pointer-events-auto w-[520px] max-w-[90vw] rounded-2xl border ${theme.bar} p-4 shadow-lg backdrop-blur`}>
                  <div className={`mb-2 flex items-center gap-1.5 text-[11px] font-semibold ${theme.headText}`}>
                    <Tags className="h-3.5 w-3.5" />
                    元素绑定
                    <span className={`font-normal ${theme.hint}`}>
                      把参考图绑到剧本名词，跨镜保持一致（对所有片段生效）
                    </span>
                  </div>
                  {bindingRows.length === 0 ? (
                    <p className={`text-[11px] leading-relaxed ${theme.hint}`}>
                      暂无锚定图。先在左侧「锚定图」面板添加角色 / 场景参考图，再回来绑定名词。
                    </p>
                  ) : (
                    <div className="max-h-[220px] space-y-2 overflow-y-auto">
                      {bindingRows.map((row) => {
                        const overLimit = row.pictureIndex > MAX_REF_PICTURES;
                        return (
                          <div key={row.key} className="flex items-center gap-2">
                            <img
                              src={cachedImageUrl(row.url)}
                              alt={row.label}
                              className="h-9 w-9 shrink-0 rounded-md border border-slate-600 object-cover"
                            />
                            <span
                              className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${
                                overLimit ? 'bg-amber-500/20 text-amber-500' : 'bg-indigo-500/20 text-indigo-400'
                              }`}
                              title={overLimit ? `超出 ${MAX_REF_PICTURES} 张上限，不会随段发给模型` : '对应的 <Picture N> 编号'}
                            >
                              图片 {row.pictureIndex}
                            </span>
                            <input
                              type="text"
                              value={bindingNames[row.key] ?? row.label}
                              onChange={(e) =>
                                setBindingNames((prev) => ({ ...prev, [row.key]: e.target.value }))
                              }
                              placeholder="剧本中的名词，如：我 / 破旧摩托车"
                              disabled={overLimit}
                              className={`min-w-0 flex-1 rounded-lg border px-2 py-1 text-[11px] outline-none disabled:opacity-40 ${theme.input}`}
                            />
                            {overLimit && (
                              <span className="shrink-0 text-[10px] text-amber-500">超限</span>
                            )}
                          </div>
                        );
                      })}
                      <p className={`pt-1 text-[10px] leading-relaxed ${theme.hint}`}>
                        图片 1 是每个片段自己的画面，逐段不同，因此不参与绑定；锚定图从图片 2 起编号。
                      </p>
                    </div>
                  )}
                </div>
              )}

              <div className={`pointer-events-auto flex items-center gap-3 rounded-2xl border ${theme.bar} px-4 py-2.5 shadow-lg backdrop-blur`}>
              <div>
                <div className={`mb-0.5 text-[10px] ${theme.hint}`}>视频模型</div>
                <select
                  value={videoModel}
                  onChange={(e) => setVideoModel(e.target.value)}
                  className={`rounded-lg border px-2 py-1 text-xs outline-none ${theme.input}`}
                >
                  {VIDEO_MODELS.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                onClick={() => {
                  setControlPanelOpen((v) => !v);
                  setBindingPanelOpen(false);
                }}
                title="精细控制：风格提示词 / 负面提示词"
                className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn} ${controlPanelOpen ? 'ring-1 ring-indigo-400' : ''}`}
              >
                <Wand2 className="h-3.5 w-3.5" /> 精细控制
                {(stylePrompt.trim() || negativePrompt.trim()) && (
                  <span className="ml-0.5 h-1.5 w-1.5 rounded-full bg-indigo-500" />
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  setBindingPanelOpen((v) => !v);
                  setControlPanelOpen(false);
                }}
                title="元素绑定：把锚定图绑到剧本名词（<Picture N>）"
                className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn} ${bindingPanelOpen ? 'ring-1 ring-indigo-400' : ''}`}
              >
                <Tags className="h-3.5 w-3.5" /> 元素绑定
                {activeBindingRows.length > 0 && (
                  <span className="ml-0.5 h-1.5 w-1.5 rounded-full bg-indigo-500" />
                )}
              </button>
              {(pendingImageNodes.length > 0 || batchRunning) && (
                <>
                  <select
                    value={candidateCount}
                    onChange={(e) => setCandidateCount(Number(e.target.value))}
                    disabled={batchRunning}
                    title="每个节点生成几张候选图供你挑一张（按张计费）"
                    className={`rounded-lg border px-2 py-1 text-xs outline-none disabled:opacity-50 ${theme.input}`}
                  >
                    <option value={1}>候选 1 张</option>
                    <option value={3}>候选 3 张</option>
                    <option value={5}>候选 5 张</option>
                  </select>
                  {!batchRunning && (
                    <button
                      type="button"
                      onClick={() => backfillMutation.mutate()}
                      disabled={backfillMutation.isPending}
                      title="从历史素材任务里按提示词找回已生成的图，不重新花钱生成（画布没保存过时用这个救回来）"
                      className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn} disabled:opacity-50`}
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      {backfillMutation.isPending ? '回填中…' : '回填已生成图'}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      if (batchRunning) {
                        batchStopRef.current = true;
                        return;
                      }
                      void runBatchTextToImage();
                    }}
                    title="把所有「有提示词但还没图」的图片节点依次生成首帧图（串行跑，避免平台限流；只花图片额度，不消耗视频额度）"
                    className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn}`}
                  >
                    {batchRunning ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> 生成中 {batchDone}/{batchTotal}（点此停止）
                      </>
                    ) : (
                      <>
                        <Wand2 className="h-3.5 w-3.5" /> 一键文生图（{pendingImageNodes.length}）
                      </>
                    )}
                  </button>
                  {!batchRunning && batchFailed > 0 && (
                    <span className="text-[10px] text-amber-500">失败 {batchFailed} 张</span>
                  )}
                  {!batchRunning && backfillCount !== null && (
                    <span className="text-[10px] text-emerald-600">
                      {backfillCount > 0
                        ? `已找回 ${backfillCount} 个节点的图`
                        : '没找到匹配的历史图（可点「一键文生图」重新生成）'}
                    </span>
                  )}
                </>
              )}
              <div className="h-8 w-px bg-slate-700" />
              <button
                onClick={() => {
                  const bad = plan.segments
                    .filter((s) => !isPublicImageUrl(s.image_url))
                    .map((s) => '「' + (s.prompt || '片段').slice(0, 16) + '」');
                  if (bad.length > 0) {
                    window.alert(
                      `以下片段的图片是本地/内网上传图，不能用于生成：\n${bad.join('\n')}\n\n请改用历史作品图，或在该图片节点点「文生图」先产出公网图。`,
                    );
                    return;
                  }
                  mutation.mutate();
                }}
                disabled={!canSubmit || mutation.isPending}
                title={
                  canSubmit
                    ? '按连线顺序逐段生成视频，模型侧自动拼接成片'
                    : '还没有可生成的片段：至少需要 1 张已生成的图片（点左边「一键文生图」）或 1 个非空文本节点'
                }
                className="rounded-xl bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {mutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  '生成成片'
                )}
              </button>
              </div>
            </div>
          </div>
        </main>
      </div>
      {/* 并发编辑提示：助手或另一个标签页改了同一张画布 —— 非阻塞，不打断编辑 */}
      {conflictBanner && (
        <div className="fixed inset-x-0 top-3 z-50 mx-auto flex w-[min(760px,94vw)] items-center gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-2.5 text-xs text-amber-900 shadow-lg">
          <span className="flex-1">
            画布已在别处被修改（服务端版本 {conflictBanner.res.serverVersion ?? '?'}，你手上基于版本
            {conflictBanner.localVersion}）。你的改动<strong>尚未保存</strong>。
          </span>
          <button
            type="button"
            onClick={() => {
              applyLocalOverServer(conflictBanner.res, conflictBanner.nodesJson, conflictBanner.edgesJson);
              setConflictBanner(null);
            }}
            className="shrink-0 rounded-lg border border-amber-400 bg-white px-2 py-1 font-medium hover:bg-amber-100"
          >
            用我的覆盖
          </button>
          <button
            type="button"
            onClick={() => {
              loadServerContent(conflictBanner.res);
              setConflictBanner(null);
            }}
            className="shrink-0 rounded-lg border border-amber-400 px-2 py-1 font-medium hover:bg-amber-100"
          >
            载入最新
          </button>
        </div>
      )}
      {externalVersion !== null && !conflictBanner && (
        <div className="fixed inset-x-0 top-3 z-40 mx-auto flex w-[min(760px,94vw)] items-center gap-3 rounded-xl border border-sky-300 bg-sky-50 px-4 py-2.5 text-xs text-sky-900 shadow-lg">
          <span className="flex-1">
            画布已在别处更新（服务端版本 {externalVersion}），你看到的可能不是最新内容。
          </span>
          <button
            type="button"
            onClick={() => void reloadFromServer()}
            className="shrink-0 rounded-lg border border-sky-400 bg-white px-2 py-1 font-medium hover:bg-sky-100"
          >
            重载最新
          </button>
        </div>
      )}
      <ChatPanel
        open={chatPanelOpen}
        onClose={() => setChatPanelOpen(false)}
        canvasId={currentProjectId}
        hasProject={currentProjectId !== null}
        dark={dark}
      />
    </div>
  );
}