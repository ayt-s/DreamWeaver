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
  type ReactFlowInstance,
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
  History,
  Sparkles,
  AlertTriangle,
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
import { qcImages } from '../api/imageQc';
import { countCandidates, outliersBySubjects } from '../api/candidateQc';
import { editImage } from '../api/imageEdit';
import { cachedImageUrl, parseImageUrls, type TaskResponse } from '../types/task';
import { createContext, useContext } from 'react';
import { reorderShotX, sortShots } from '../utils/shotOrder';
import { filmSecondsFromChain } from '../utils/canvasPlan';
import {
  augmentPromptWithAnchors,
  firstFrameRefsFor,
  MAX_REF_PICTURES,
  parseAnchorRefs,
  pickUrlsByPrompt,
  serializeAnchorRefs,
  urlMapOf,
  type AnchorMap,
} from '../utils/anchors';
import {
  bindingBadge,
  bindingIndexesInSegments,
  segmentRefImages,
} from '../utils/segmentRefs';
import { recomposePrompts, type RecomposeResult } from '../api/promptRecompose';
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

/** 取提示词里的某一段（`；` 分段，段首形如 `[角色锚]`）—— 重算前后对照用。 */
function blockOf(prompt: string, prefix: string): string {
  return prompt.split('；').find((p) => p.startsWith(prefix)) ?? '';
}

/** 超长文本截断（面板里只给一眼能看完的对照长度）。 */
function shortText(s: string, n = 140): string {
  const t = s.trim();
  return t.length > n ? `${t.slice(0, n)}…` : t;
}

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
  /**
   * 来源：从「历史作品/画布素材」加入画布时记下的源任务（只读，**不参与生成**）。
   * 只作为追溯信息随画布保存 —— 用来回答「这张图是哪来的、当初写的什么」。
   */
  originTaskId?: number;
  /** 来源任务的原始提示词（只读）；点节点上的「填入提示词」才会写进 prompt 字段 */
  originPrompt?: string;
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

/** 把参考图数组编码成 Java 侧要的 JSON 字符串；空数组 = 不传（保持旧行为）。 */
function refImagesField(refs: string[]): string | undefined {
  return refs.length > 0 ? JSON.stringify(refs) : undefined;
}

/**
 * 文生图单张：提交 text_image 任务并轮询到完成，返回图片 URL。
 *
 * 抽成模块级函数是为了「一键文生图」批量流程与单节点按钮共用同一套超时/失败语义，
 * 免得两处各写一套轮询逻辑、各自演化。
 *
 * `referenceImages`：锚定图（角色在前、场景在后）。传了就变图生图 ——
 * 2026-09-18 A/B 实测场景侧明显收益、角色侧弱收益，空数组不传即旧行为。
 */
async function generateOneImage(
  prompt: string,
  count = 1,
  ratio = '16:9',
  timeoutMs = 180_000,
  referenceImages: string[] = [],
): Promise<string[]> {
  const res = await createVideoTask({
    prompt,
    genType: 'text_image',
    // 直出图：跳过 agent 侧需求解析/剧本/分镜，一次出 count 张同 prompt 候选
    directImage: true,
    imageCount: count,
    // ★ 画幅必须传：不传 agnes 按 1:1 出图（实测 1024×1024 正方形，
    //   而视频是 16:9）—— 首帧是画面的真正基底，画幅错了整条链都错
    imageRatio: ratio || '16:9',
    // ★ 锚定图当参考图（2026-09-18 A/B 实测）：场景侧明显收益（把参考场景带进首帧），
    //   角色侧弱收益。空数组不传，行为与改动前逐字一致。
    referenceImages: refImagesField(referenceImages),
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

/**
 * 把锚定图（含描述）传给画布节点组件。
 *
 * 节点组件只收 `{id, data}`（React Flow 的契约），而锚定图是**页面级**状态；
 * 又**不能塞进 `data`**（data 是业务 JSON，会落库、会被序列化 —— 见画布四条硬规则）。
 * 所以走 context：页面在 `<ReactFlow>` 外层提供，节点里 `useContext` 取。
 */
const AnchorsCtx = createContext<{ chars: AnchorMap; scenes: AnchorMap }>({
  chars: {},
  scenes: {},
});

/** 定点修正默认追加的「保真」尾句（2026-09-18，用户实测驱动）。
 *
 * 用户实际用这个功能时的反馈：指令只写「去掉右边的黑牛」，结果**除了去掉牛，
 * 别的地方也变了** —— 真实 [角色锚] 复杂镜头比我做 A/B 用的「两头牛」合成图更容易漂。
 *
 * 而我的 A/B 里带上「其余全部保持不变」这句时，实测人物长相、服装、姿势、场景、光线、
 * 构图、画幅**全部保持**，只改了目标那一处。所以这里默认补上，用户不必懂怎么写指令。
 *
 * 想整张重画应该用「文生图」，按钮文案里已写清这一点。
 */
const FIX_PRESERVE_CLAUSE =
  '；除上述改动外，画面其余部分（人物长相、服装、姿势、背景、光线、构图、画幅）全部保持不变，不要重画整张图';

/** 缩略图拿不到时的占位：**不留无声的破图**，tooltip 说清下一步该动哪里。
 *
 * 为什么要有（2026-09-18）：agnes 对产物 URL 的保留**官方零承诺**（我们实测 175/175
 * 长期存活、URL 无签名），所以不做下载归档（成本不成比例），只在展示层兜底。
 * ⚠️ 文案必须指向**真正的下一跳**：锚定图失败 ≠ 产物过期（它的下一步是重新生成/重选锚定图），
 * 一律写「网络异常」或指错组件，比没有文案更糟（本项目已有教训）。
 */
function ThumbFailed({
  title,
  className = 'h-10 w-10 rounded object-cover',
}: {
  title: string;
  className?: string;
}) {
  return (
    <span
      className={`flex shrink-0 items-center justify-center bg-slate-400/25 text-center text-[9px] leading-3 text-slate-500 dark:text-slate-300 ${className}`}
      title={title}
    >
      图失效
    </span>
  );
}

function ImageNodeView({ id, data }: NodeProps<GraphNode>) {
  const { updateNodeData, getNodes, setNodes } = useReactFlow();
  const queryClient = useQueryClient();
  const anchors = useContext(AnchorsCtx);
  const fileRef = useRef<HTMLInputElement>(null);
  const [generating, setGenerating] = useState(false);
  const [status, setStatus] = useState('');
  // 图像定点修正（图生图）：对着当前这张图做定向修改，不重新生成。
  // 实测定位：删/换局部可靠；改景别会连带重画人物外观；对「锁脸」无优势。
  const [fixText, setFixText] = useState('');
  const [fixBusy, setFixBusy] = useState(false);
  const [fixStatus, setFixStatus] = useState('');
  // 破图兜底（2026-09-18）：产物 URL 可能拿不到 —— agnes 对产物保留**官方零承诺**，
  // 我们只实测到 175/175 长期存活。不留无声破图，文案指向真正的下一跳。
  const [brokenMedia, setBrokenMedia] = useState<Record<string, true>>({});
  const markBroken = (key: string) =>
    setBrokenMedia((m) => (m[key] ? m : { ...m, [key]: true }));
  // 候选图质检（P0-1）：命中「严禁面部特写」红线的候选打标提示。
  // ⚠️ 只打标不自动淘汰 —— 标定样本还不够（正样本 6 张），见 image_qc.py 的说明。
  const candidateKey = ((data.candidates as string[] | undefined) ?? []).join('|');
  const { data: qc } = useQuery({
    queryKey: ['image-qc', candidateKey],
    enabled: candidateKey.length > 0,
    // 同一个 URL 的判定是确定的（本地人脸检测），缓存到会话结束即可
    staleTime: Infinity,
    queryFn: () => qcImages(data.candidates as string[]),
  });
  const qcByUrl = new Map((qc?.results ?? []).map((r) => [r.url, r]));
  // 候选主体计数（多模态，2026-09-18 标定 13/13）：治「三张候选一起多出一头牛」。
  // 串行调用，角标会比缩略图晚 15~25s 到 —— 晚到没关系，失败就什么都不显示。
  const candidateUrls = (data.candidates as string[] | undefined) ?? [];
  const { data: subjects } = useQuery({
    queryKey: ['candidate-subjects', candidateKey],
    // ⚠️ 只有 1 张候选时不调：没有「与其余不同」可比，白烧一次模型调用
    //   （画布上 6 个节点各一次 = 6 次；这也是与候选块「length > 1 才渲染」同一口径）
    enabled: candidateKey.length > 0 && candidateUrls.length > 1,
    // temperature=0 + 同一批 URL → 判定稳定，缓存到会话结束
    staleTime: Infinity,
    queryFn: () => countCandidates(candidateUrls),
  });
  const subjectsByUrl = new Map((subjects?.results ?? []).map((r) => [r.url, r]));
  // 「与其余候选不同」的那些（相对多数口径，只提示不淘汰）
  const subjectOutliers = outliersBySubjects(subjects ?? null);
  const patch = (p: Partial<ImageNodeData>) => updateNodeData(id, p);

  // === 分镜顺序（上移 / 下移）===
  // 成片顺序 = 图片节点**从左到右的 x 坐标**（唯一判据；后端 agent 的 reorder_shots
  // 用的是同一条规则）。拖动节点确实能改顺序，但要把 6~10 个节点拖到互相精确的前后
  // 位置很难（间距不齐时尤其），所以给「第 N 段」徽标配了 ▲▼。
  // 排序/换位的规则抽在 utils/shotOrder.ts（纯函数 + 单测，因为排错了不会报错，
  // 只会让成片顺序不对）。
  const ORDER = (data as { __order?: number }).__order ?? 0;
  // ★ 2026-09-19 修（#26）：段号与"位次"是两件事，拆开读 ——
  //   `__order` = 成片里的第几段（只有**会真正提交**的节点才有，见下面注入处）；
  //   `__place` = 该节点在 chain 里的位次（所有图片节点都有）⇒ 驱动 ▲▼ 与禁用边界。
  //   原来共用一个值，导致没放图的节点也顶着「第 N 段」的假段号。
  const PLACE = (data as { __place?: number }).__place ?? 0;
  const PLACE_TOTAL = (data as { __placeTotal?: number }).__placeTotal ?? 0;

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
  /** 定点修正：对着当前这张图做定向修改（图生图），不重新生成一张。
   *
   * 实测定位（2026-09-18，见 api/imageEdit.ts）：**删/换局部可靠** —— 底图「两头黑牛」
   * + 指令「只保留一头，其余不变」→ 结果只剩一头，且人物/服装/姿势/场景/光线/构图全部保持；
   * 而「拉远为中远景」这类全局改动会**连带重画人物外观**；i2i 对「锁脸」也没有优势
   * （两轮实测）。所以按钮 title 里写明用途，免得用户拿它去治「大脸」换来一张换了人的图。
   */
  const onFixImage = async () => {
    const instruction = fixText.trim();
    const src = (data.imageUrl || '').trim();
    if (!src) {
      setFixStatus('先在左边出图（或选一张候选）再修');
      return;
    }
    if (!instruction) {
      setFixStatus('写清要改什么，如「去掉多出来的那头牛」');
      return;
    }
    setFixBusy(true);
    setFixStatus('修正中…');
    try {
      // 默认追加「保真」尾句（去掉用户可能多打的句末标点，避免出现「。。」）
      const guarded = `${instruction.replace(/[。.；;，,\s]+$/, '')}${FIX_PRESERVE_CLAUSE}`;
      const urls = await editImage(src, guarded, { ratio: data.ratio });
      applyEditResult(src, urls, '改好');
      setFixText('');
    } catch (err) {
      setFixStatus(err instanceof Error ? err.message : '修正失败');
    } finally {
      setFixBusy(false);
    }
  };

  /** 把修正/补画幅的结果并入候选并设为本镜首帧（原图留着，可比对可回退）。
   *
   * ⚠️ 原图**必须**一起放进候选：候选块是 `length > 1` 才渲染的（见下方「候选 N 张」），
   * 只放新图会让原图直接从界面上消失 —— 既没法左右对比，也没法退回改坏的版本。
   */
  const applyEditResult = (src: string, urls: string[], label: string) => {
    if (urls.length === 0) throw new Error('模型没有返回图片');
    const prev = (data.candidates as string[] | undefined) ?? [];
    const merged = [...prev, src, ...urls].filter((u, i, a) => a.indexOf(u) === i);
    patch({ candidates: merged, imageUrl: urls[0] });
    setFixStatus(`${label} ${urls.length} 张，已设为首帧（基于当前选中那张图；候选里可对比/回退）`);
  };

  /** 补画幅（扩画幅）：把当前图的比例补齐到本节点 ratio。
   *
   * 为什么需要（2026-09-18 实测）：keyframe 视频的几何**跟随首帧比例**，而老项目
   * （39/40）的图是 1:1（出图比例修复之前生成的）→ 那些段的视频是 704x704 方的。
   * 补画幅 = 本地补边 + i2i 让模型把两侧画成场景延续（实测两侧自然、无拼接痕迹），
   * 之后拿它当首帧就能出宽屏视频，**不必重出图**（重出图会刷掉用户已认可的图）。
   *
   * ⚠️ 代价（实测，按钮文案里也写）：模型会顺带把中间**重新构图为更宽的景别**
   * —— 人物/场景/动作都在，但脸会变小。所以不是无副作用的比例转换。
   */
  const onPadImage = async () => {
    const src = (data.imageUrl || '').trim();
    if (!src) {
      setFixStatus('先在左边出图（或选一张候选）再补画幅');
      return;
    }
    const target = data.ratio || '16:9';
    setFixBusy(true);
    setFixStatus('补画幅中…');
    try {
      const urls = await editImage(src, '', { ratio: target, padToRatio: target });
      applyEditResult(src, urls, `补成 ${target}`);
    } catch (err) {
      setFixStatus(err instanceof Error ? err.message : '补画幅失败');
    } finally {
      setFixBusy(false);
    }
  };

  const startTextToImage = async () => {
    // ★ P0-1：把本节点提示词里提到的角色/场景**描述**拼进去。
    // 描述仍然有用（它对「名字 → 长相」的约束比图更明确），但 2026-09-18 A/B 实测
    // 说明它不够：同一提示词下，**带锚定图当参考图**的产物能把参考图里的村庄/梯田
    // 环境带出来（纯文生只有普通山坡），角色服装色系也更贴角色卡。
    // ⚠️ 但别指望它锁脸 —— i2i 对「跨镜脸一致」没有优势（两轮实测一致）。
    const rawPrompt = (data.prompt || '').trim();
    const prompt = augmentPromptWithAnchors(rawPrompt, anchors.chars, anchors.scenes);
    if (!prompt) {
      setStatus('请先填写提示词');
      return;
    }
    // 参考图用**原始**提示词算命中的锚定图：augment 后的文本里必然含锚定图名字，
    // 拿它匹配会「自证命中」（agent 侧 `_SEG_TEXT_FIELDS` 刻意排除已生成的提示词，同一道理）。
    const refs = firstFrameRefsFor(
      rawPrompt,
      urlMapOf(anchors.chars),
      urlMapOf(anchors.scenes),
    );
    setGenerating(true);
    setStatus(refs.length > 0 ? `文生图进行中（带 ${refs.length} 张锚定图参考）…` : '文生图进行中…');
    try {
      const res = await createVideoTask({
        prompt,
        genType: 'text_image',
        // ⚠️ 必须走直出短路（与「一键文生图」一致）：不传 directImage 时 Java 不加
        // `direct_image`，agent 会**按 prompt 重新拆镜** → 一次白出 3~5 张不同画面的图，
        // 而这里只用得上第 1 张（实测过的额度浪费，见 TaskServiceImpl.java:380-383）。
        directImage: true,
        // 画幅取本节点的 ratio（不传 = agnes 默认 1:1 正方形）
        imageRatio: data.ratio || '16:9',
        referenceImages: refImagesField(refs),
        // ★ 2026-09-19 修（#23·#31）：**必须带素材标记**，与批量路径（:210）保持一致。
        //   漏传时这条任务以 `default` 落库，于是两头都不对：
        //   ① 左侧「本画布素材」面板里找不到它（面板按 source=canvas_asset 过滤，
        //      上面那句 invalidateQueries 是空转）；② 画廊「草稿」筛选下多出一条
        //      与本次创作无关的记录 —— 与代码注释和产品意图正好相反。
        source: 'canvas_asset',
      });
      const taskId = Number(res.id);
      const t0 = Date.now();
      // ★ 2026-09-19 修（#28）：轮询到期必须有**终态**，否则状态永远停在「文生图进行中…」
      //   （排队 + 出图经常超过 90 秒）—— 用户以为卡死，往往会再点一次 ⇒ 重复出图白花钱。
      //   90 秒上限本身是既定行为，本次只补"到期后的出口与说明"。
      let settled = false;
      while (Date.now() - t0 < 90_000) {
        await new Promise((r) => setTimeout(r, 4000));
        const cur: TaskResponse | null = await getTask(taskId);
        if (!cur) break;
        if (cur.status === 'completed') {
          const urls = parseImageUrls(cur.imageUrls);
          if (urls.length > 0) {
            patch({ imageUrl: urls[0] });
            setStatus('已生成参考图');
            // 这条任务同样是画布素材 → 让左侧「从历史作品选取」立刻能选到它
            queryClient.invalidateQueries({ queryKey: HISTORY_IMAGES_KEY });
          } else {
            setStatus('生成完成但无图片');
          }
          settled = true;
          break;
        }
        if (cur.status === 'failed' || cur.status === 'expired') {
          setStatus('生成失败：' + (cur.errorMessage || '未知原因'));
          settled = true;
          break;
        }
        // 已中断：任务已停下（后端可能稍后自动续跑），不要一直卡在「进行中…」
        if (cur.status === 'interrupted') {
          setStatus('任务已中断，Agent 将在后台自动恢复续跑，可稍后刷新查看');
          settled = true;
          break;
        }
      }
      if (!settled) {
        setStatus(`生成超时（已等待 90 秒，任务 id ${taskId}）：可能仍在排队，可稍后到画廊查看或重试`);
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
        {PLACE > 0 ? (
          <span className="ml-auto flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              className="nodrag rounded px-0.5 text-[10px] text-indigo-500 hover:bg-indigo-50 disabled:opacity-25"
              disabled={PLACE <= 1}
              title="前移一段（在成片里提前）"
              onClick={() => moveShot(-1)}
            >
              ▲
            </button>
            <span
              className="rounded-full bg-indigo-100 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600"
              title="成片里的第几段（按画布从左到右排序）"
            >
              {ORDER > 0 ? `第 ${ORDER} 段` : '待放图（不参与成片）'}
            </span>
            <button
              type="button"
              className="nodrag rounded px-0.5 text-[10px] text-indigo-500 hover:bg-indigo-50 disabled:opacity-25"
              disabled={PLACE_TOTAL === 0 || PLACE >= PLACE_TOTAL}
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
          brokenMedia[`main:${data.imageUrl}`] ? (
            <div className="flex flex-col items-center justify-center gap-1 px-3 text-center">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              <div className="text-[11px] font-medium text-slate-600">首帧图加载失败</div>
              <p className="text-[10px] leading-tight text-slate-500">
                产物可能已被清理，点下方「文生图」可重出这一镜
              </p>
            </div>
          ) : (
            <img
              src={cachedImageUrl(data.imageUrl)}
              alt="参考图"
              className="h-full w-full object-contain"
              onError={() => markBroken(`main:${data.imageUrl}`)}
            />
          )
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
            {qc && qc.summary.closeupCount > 0 && (
              <span className="ml-1 text-amber-600">
                · {qc.summary.closeupCount} 张疑似面部特写
              </span>
            )}
            {/* 主体计数是模型算的（慢一步到）：只提示「这张跟别张数出来不一样」 */}
            {subjectOutliers.size > 0 && (
              <span className="ml-1 text-amber-600">
                · {subjectOutliers.size} 张主体数与其余不同
              </span>
            )}
          </div>
          <div className="flex gap-1 overflow-x-auto pb-0.5">
            {(data.candidates as string[]).map((u: string, i: number) => {
              const verdict = qcByUrl.get(u);
              const bad = verdict?.closeup === true;
              const subject = subjectsByUrl.get(u);
              const subjectLabel = subject && !subject.skipped ? subject.label : '';
              const subjectOdd = subjectOutliers.has(u);
              return (
                <button
                  key={`${id}-cand-${i}`}
                  type="button"
                  onClick={() => patch({ imageUrl: u })}
                  title={
                    `候选 ${i + 1}（点击作为该镜首帧）` +
                    (subjectLabel ? ` · 画面里 ${subjectLabel}` : '') +
                    (subjectOdd ? ' · 与其余候选数出的主体数不同' : '') +
                    (bad
                      ? ` · 疑似面部特写：人脸占画面 ${Math.round((verdict?.faceSpan ?? 0) * 100)}%，` +
                        `提示词里的「严禁面部特写」红线可能没拦住`
                      : '')
                  }
                  className={`relative h-11 w-11 shrink-0 overflow-hidden rounded border transition ${
                    data.imageUrl === u
                      ? 'border-indigo-500 ring-1 ring-indigo-400'
                      : 'border-slate-200 hover:border-indigo-300'
                  }`}
                >
                  {brokenMedia[`cand:${u}`] ? (
                    <span
                      className="flex h-full w-full items-center justify-center bg-slate-200 text-center text-[8px] leading-3 text-slate-500"
                      title="这张候选拿不到（产物可能已被清理）—— 换一张候选，或点「文生图」重出"
                    >
                      图失效
                    </span>
                  ) : (
                    <img
                      src={cachedImageUrl(u)}
                      alt={`候选 ${i + 1}`}
                      className="h-full w-full object-cover"
                      onError={() => markBroken(`cand:${u}`)}
                    />
                  )}
                  {/* 命中红线的候选角标：⚠️ 只提示、不禁用（标定样本还不够，见 image_qc.py） */}
                  {bad && (
                    <span className="absolute inset-x-0 bottom-0 bg-amber-500/90 text-center text-[8px] font-medium leading-3 text-white">
                      面部特写
                    </span>
                  )}
                  {/* 主体数角标（模型数的，慢一步到）：与其余候选不同时用琥珀底 —— 
                      治「三张候选一起多出一头牛，肉眼挑不出来」。⚠️ 同样只提示不淘汰 */}
                  {subjectLabel && (
                    <span
                      className={`absolute inset-x-0 top-0 text-center text-[8px] font-medium leading-3 text-white ${
                        subjectOdd ? 'bg-amber-500/90' : 'bg-slate-900/55'
                      }`}
                    >
                      {subjectLabel}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {/* 定点修正（图生图）：对着当前这张图改一处，不重新生成。
          实测定位 —— 删/换局部可靠（「只保留一头牛」会照做且其余保持），
          改景别会连带重画人物外观，对「锁脸」也没优势；所以文案只承诺「改一处」。
          本地/内网图也支持（agent 侧会抓回来转 base64，agnes 拉不到 localhost）。 */}
      {data.imageUrl && (
        <div className="mb-2">
          <div className="flex gap-1">
            <input
              className="nodrag min-w-0 flex-1 rounded border border-slate-200 px-1.5 py-1 text-[10px] text-slate-700 outline-none focus:border-amber-400"
              placeholder="改一处：如「去掉多出来的那头牛」"
              value={fixText}
              onChange={(e) => setFixText(e.target.value)}
              onKeyDown={(e) => {
                // 别让画布吃掉按键（Delete 会删节点、空格会平移）
                e.stopPropagation();
                if (e.key === 'Enter') void onFixImage();
              }}
            />
            <button
              type="button"
              className="nodrag shrink-0 rounded bg-amber-500 px-1.5 py-1 text-[10px] font-medium text-white hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={fixBusy || !fixText.trim()}
              onClick={() => void onFixImage()}
              title="改一处（适合删/换多出来的主体）；拉远镜头或整张重画请用「文生图」"
            >
              {fixBusy ? '修正中…' : '修一下'}
            </button>
            {/* 补画幅：给「图是方的、视频也跟着方」这类几何问题兜底。
                实测：i2i 能把补出来的两侧画成场景延续（无拼接痕迹），
                但会顺带把中间重新构图为更宽的景别 —— title 里写明代价。 */}
            <button
              type="button"
              className="nodrag shrink-0 rounded border border-slate-200 px-1.5 py-1 text-[10px] font-medium text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300"
              disabled={fixBusy}
              onClick={() => void onPadImage()}
              title={
                `把这张图补齐成 ${data.ratio || '16:9'} 画幅（本地补边 + 让模型把两侧画成场景延续）。` +
                '用于修「图是 1:1 → keyframe 视频也是方的」。\n' +
                '⚠️ 会顺带把中间重新构图为更宽的景别（人物/场景在，但脸会变小）。'
              }
            >
              补成 {data.ratio || '16:9'}
            </button>
          </div>
          {/* 一行提示兼状态位：没有状态时说明默认行为（用户实测反馈「别处也变了」之后加的），
              有状态时直接显示状态，不额外占一行 */}
          <div className="mt-1 text-[10px] leading-3 text-slate-500">
            {fixStatus || '默认只改这处、其余保持不变（整张重画用「文生图」）'}
          </div>
        </div>
      )}
      {data.imageUrl && !isPublicImageUrl(data.imageUrl) && (
        <div className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
          本地上传图仅可预览，生成需公网图：请用历史作品或点「文生图」生成
        </div>
      )}

      {/* 来源（只读）：从「历史作品/画布素材」入画布时记下的源任务。
          刻意**不自动写进 prompt**：那套 [角色锚]/[镜头] 是给图模型的静态设定，
          而图生视频要的是动作描述，塞进去还会踩「列了谁就画谁/人数措辞凑人数」的坑。
          要不要用，由用户点一下「填入提示词」决定（填进去的是可编辑文本，源任务那份永远只读） */}
      {data.originPrompt && (
        <div className="mb-2 rounded-lg border border-slate-200 bg-slate-50 p-2">
          <div className="mb-1 flex items-center gap-1.5 text-[10px] text-slate-500">
            <History className="h-3 w-3" />
            来源 #{data.originTaskId}
            <button
              type="button"
              className="nodrag ml-auto rounded border border-slate-300 px-1.5 py-0.5 text-[10px] text-slate-600 hover:bg-slate-200"
              title="把来源提示词填进本节点的提示词（填进去后可自由编辑）"
              onClick={() => {
                if (data.prompt.trim() && !window.confirm('会覆盖本节点现有提示词，继续？')) {
                  return;
                }
                patch({ prompt: data.originPrompt ?? '' });
              }}
            >
              填入提示词
            </button>
          </div>
          <div className="max-h-16 overflow-y-auto whitespace-pre-wrap text-[10px] leading-relaxed text-slate-500">
            {data.originPrompt}
          </div>
        </div>
      )}
      {/* 空提示词的后果必须说清：agent 侧会兜底成「对参考图内容做缓慢推进的动态运镜」
          （storyboard.py:150-153）。不说的话，用户会以为画布上的提示词真的在起作用 */}
      {data.imageUrl && !(data.prompt || '').trim() && (
        <div className="mb-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[10px] text-slate-500">
          未填提示词 → 提交时用通用运镜兜底（对参考图缓慢推进）
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

/**
 * 画布「从历史作品选取」的取数 key。
 * 生成完成后要 invalidate 它（单节点文生图 / 一键文生图批量），写死两处容易漂。
 */
const HISTORY_IMAGES_KEY = ['canvas-history-images'] as const;

/** 面板一页 12 格（3 列 × 4 行）；「加载更多」按页累加 */
const HISTORY_PAGE_SIZE = 12;
/** 面板最多展示 48 张（后端 size 上限 50）；再多去画廊看，别把窄面板堆成无限长 */
const HISTORY_MAX_SIZE = 48;
/** 分源：素材是画布自己生成的（数量多），作品是画廊里的 —— 不分开的话素材会把 12 格占满 */
const HISTORY_TABS: Array<{ key: 'asset' | 'work'; label: string }> = [
  { key: 'asset', label: '本画布素材' },
  { key: 'work', label: '作品' },
];

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
  // 首帧锁定（默认开）：把每段首帧图当视频的**实际第一帧**（agnes keyframe 模式）。
  // 官方对 reference 模式的定义是「内容/风格/运动参考，**可能重新构图、重新计时**」——
  // 于是此前那张首帧图根本不是视频起点（用户看到的「视频和我的图不像」是预期行为）。
  // 关掉 = 退回 reference 模式（首帧图只当参考图）。
  const [lockFirstFrame, setLockFirstFrame] = useState(true);
  // 段间衔接（默认**关**，2026-09-18 A/B + 目视复核定档）：用下一段的首帧当本段尾帧。
  //
  // ⚠️ 这里有个「指标被耍」的教训，别只看数字就改默认值：
  //   数字上：关 → 接缝差 69.19/段内 26.80 = 2.58；开 → 31.54/22.51 = 1.40（跳变减半）
  //   但看图发现：开启后**第 1 段自己的内容被毁**——它的提示词是「黄昏、余烬未熄、
  //   黑烟仍在上升」，画面却已被拖成下一段的「阳光草坡 + 绿袍少年」。
  //   也就是说 2.58 那个「跳变」本来就是**两个不同场景之间的正常切换**，抹平它
  //   靠的是牺牲本段与提示词的一致性 —— 对分镜式内容（每段各自一个场景）是净损失。
  //
  // 所以默认关；只有「同一地点的一段连续动作被切成多段」时才值得开（那时连续性
  // 才是真需求）。开关的 title 里写明了适用场景。
  const [chainFrames, setChainFrames] = useState(false);
  const [controlPanelOpen, setControlPanelOpen] = useState(false);
  // 元素语义绑定：名词 → 参考图编号（<Picture N>），key = 锚定图标识，value = 剧本中的名词
  const [bindingNames, setBindingNames] = useState<Record<string, string>>({});
  const [bindingPanelOpen, setBindingPanelOpen] = useState(false);
  // 面板侧的破图兜底（与节点内同一口径）。⚠️ 锚定图失败 ≠ 产物过期：
  // 它的下一步是「重新生成/重选锚定图」，所以文案与产物那套**不共用**。
  const [brokenRefs, setBrokenRefs] = useState<Record<string, true>>({});
  const markRefBroken = (key: string) =>
    setBrokenRefs((m) => (m[key] ? m : { ...m, [key]: true }));
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
  // React Flow 实例：新节点要落在「当前视口中心」，要用它做屏幕→画布坐标换算（见 spawnPosition）。
  // 页面本身在 <ReactFlow> 之外，拿不到 ReactFlowProvider 的 context，只能用 onInit 存实例。
  const rfRef = useRef<ReactFlowInstance<GraphNode, Edge> | null>(null);
  // ★ 2026-09-19 修（#27）：`?anchorRefs` 是「小说转画布」的一次性带入，**只属于进入时那个项目**。
  //   原样从不清理 ⇒ 下拉切到别的画布项目后，这套锚定图仍是
  //   `effectiveCharRefs / effectiveSceneRefs` 的兜底 ⇒ 会被当参考图参与那个项目的提交；
  //   更糟的是：只要在那个项目里做任何锚定图增删，就会把这份外来图**写进它的
  //   character_refs / scene_refs**（污染别的项目的锚定图数据）。
  //   所以在项目切换时把参数从 URL 清掉；留在原项目里时照常工作。
  const anchorRefsParam = searchParams.get('anchorRefs');
  const anchorRefsProjectRef = useRef<number | null>(null);
  useEffect(() => {
    if (!anchorRefsParam) return;
    if (anchorRefsProjectRef.current === null) {
      anchorRefsProjectRef.current = currentProjectId;   // 记住带入时的项目
      return;
    }
    if (anchorRefsProjectRef.current !== currentProjectId) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete('anchorRefs');
          return next;
        },
        { replace: true },
      );
      anchorRefsProjectRef.current = null;
    }
  }, [anchorRefsParam, currentProjectId, setSearchParams]);

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
  // 锚定图状态用 `{url, desc?}`：desc 是生成锚定图时那段设定，**首帧文生图要用它**
  // （图接口不吃图片输入，只能靠文字）。存取都经 serialize/parseAnchorRefs，与旧的
  // 纯 url 格式双向兼容 —— 存量画布不用迁移。
  const [anchorCharRefs, setAnchorCharRefs] = useState<AnchorMap>({});
  const [anchorSceneRefs, setAnchorSceneRefs] = useState<AnchorMap>({});
  const [charRefName, setCharRefName] = useState('');
  const [charRefUrl, setCharRefUrl] = useState('');
  const [sceneRefName, setSceneRefName] = useState('');
  const [sceneRefUrl, setSceneRefUrl] = useState('');
  const [regenerating, setRegenerating] = useState<{ kind: 'char' | 'scene'; name: string } | null>(null);
  // 重新生成的**候选**：生成完先放这里，面板里新旧并排让用户选，选「用新图」才覆盖。
  // 锚定图是全片一致性的基准，旧图一覆盖就再也拿不回来（此前就是直接覆盖）。
  const [anchorCandidate, setAnchorCandidate] = useState<{
    kind: 'char' | 'scene';
    name: string;
    url: string;
    description: string;
  } | null>(null);
  // 每个锚定图的描述草稿（key = `char:名字` / `scene:名字`）——默认填已存描述或名字，
  // 可以改完再点重新生成；改完的描述会随结果一起存下来（下次不用重打）。
  const [descDrafts, setDescDrafts] = useState<Record<string, string>>({});
  const anchorRefBox = useRef<HTMLDivElement>(null);

  // 元素语义绑定（④）：参考图编号必须与提交时的组装顺序严格一致，
  // 否则会绑错对象。组装规则（见 mutation）：
  //   每段 reference_images = [本段图, ...角色锚定图, ...场景锚定图]，截断 5 张
  // 因此 Picture 1 = 每段自己的图（逐段不同，不可全局绑定），Picture 2 起才是锚定图。
  // 锚定图来源与提交保持一致：优先画布 state，URL anchorRefs 兜底。
  const effectiveCharRefs: AnchorMap = useMemo(
    () =>
      Object.keys(anchorCharRefs).length > 0
        ? anchorCharRefs
        : parseAnchorRefs(anchorRefs?.characters),
    [anchorCharRefs, anchorRefs],
  );
  const effectiveSceneRefs: AnchorMap = useMemo(
    () =>
      Object.keys(anchorSceneRefs).length > 0
        ? anchorSceneRefs
        : parseAnchorRefs(anchorRefs?.scenes),
    [anchorSceneRefs, anchorRefs],
  );
  // 纯 url 视图：提交载荷（reference_images）与面板缩略图仍按 url 处理
  const effectiveCharUrls = useMemo(() => urlMapOf(effectiveCharRefs), [effectiveCharRefs]);
  const effectiveSceneUrls = useMemo(() => urlMapOf(effectiveSceneRefs), [effectiveSceneRefs]);
  // 传给画布节点组件的锚定图（含描述，首帧文生图要用）。必须 useMemo ——
  // 每次渲染新建对象会让所有节点重渲染。
  const anchorsCtxValue = useMemo(
    () => ({ chars: effectiveCharRefs, scenes: effectiveSceneRefs }),
    [effectiveCharRefs, effectiveSceneRefs],
  );
  // 元素绑定的行与编号见下面「plan 之后」的 bindingRows ——
  // 编号必须按**各段真实数组**现算（依赖 plan.segments），所以定义位置挪到了 plan 之后。

  // 切换项目时，从项目数据同步锚定图 state
  useEffect(() => {
    if (!currentProjectId) {
      setAnchorCharRefs({});
      setAnchorSceneRefs({});
      return;
    }
    const p = projects.find((x) => x.id === currentProjectId);
    if (!p) return;
    // 解析交给 utils/anchors.ts：兼容旧的「纯 url」与新的「{url, desc}」两种存储格式
    setAnchorCharRefs(parseAnchorRefs(p.characterRefs));
    setAnchorSceneRefs(parseAnchorRefs(p.sceneRefs));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProjectId, projects]);

  // URL ?anchorRefs=<base64> 自动合并到当前画布的锚定图 state
  // 优先级：URL anchorRefs > 画布现有 anchorRefs
  // 必须在项目加载完成后执行，否则 currentProjectId 还没设置
  useEffect(() => {
    if (!anchorRefs || !currentProjectId) return;
    // URL 载荷可能是旧的「名字 → url」或新的「名字 → {url, desc}」（转画布时带上描述），
    // parseAnchorRefs 两种都吃。
    const chars = parseAnchorRefs(anchorRefs.characters);
    const scenes = parseAnchorRefs(anchorRefs.scenes);
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
  const saveAnchorsToProject = async (charRefs: AnchorMap, sceneRefs: AnchorMap) => {
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
    // 序列化交给 utils/anchors.ts：**没有描述时写回旧的纯 url 格式**（不让存量数据变形）
    const charJson = serializeAnchorRefs(charRefs);
    const sceneJson = serializeAnchorRefs(sceneRefs);
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
    const next = { ...anchorCharRefs, [name]: { url } };
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
    const next = { ...anchorSceneRefs, [name]: { url } };
    setAnchorSceneRefs(next);
    setSceneRefName('');
    setSceneRefUrl('');
    try {
      await saveAnchorsToProject(anchorCharRefs, next);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '保存失败');
    }
  };

  // 重新生成单个锚定图：描述由**面板内联输入**传入。
  // 不再用 window.prompt —— 阻塞式弹框，且部分 Electron/webview 里压根不可用
  // （症状是「点了没反应」）；默认值本该是可见、可改的输入框。
  // 生成结果只做**候选**（见 anchorCandidate），不立刻覆盖。
  const regenerateAnchor = async (kind: 'char' | 'scene', name: string, desc: string) => {
    const description = desc.trim() || name;
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
      // ★ 只做候选：面板里新旧并排，点「用新图」才落库 ——
      //   锚定图是全片一致性的基准，旧图覆盖掉就再也回不来，而重新生成的结果常常更差。
      setAnchorCandidate({ kind, name, url: newUrl, description });
    } catch (e) {
      window.alert(e instanceof Error ? `重新生成失败：${e.message}` : '重新生成失败');
    } finally {
      setRegenerating(null);
    }
  };

  /** 采用候选锚定图：此刻才写入并落库（在此之前一直保留旧图）。 */
  const applyAnchorCandidate = async () => {
    if (!anchorCandidate) return;
    const { kind, name, url, description } = anchorCandidate;
    if (kind === 'char') {
      const next = { ...anchorCharRefs, [name]: { url, desc: description } };
      setAnchorCharRefs(next);
      await saveAnchorsToProject(next, anchorSceneRefs);
    } else {
      const next = { ...anchorSceneRefs, [name]: { url, desc: description } };
      setAnchorSceneRefs(next);
      await saveAnchorsToProject(anchorCharRefs, next);
    }
    setAnchorCandidate(null);
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
    // ★ 2026-09-19 修（#21）：「每段时长」是**全链**设置，必须先扫出来再用。
    //   原来是边遍历边赋值，而成片节点在 chain 里排在图片节点**之后** ⇒ 每段 push 时
    //   拿到的仍是初值 4：用户改成 6 秒、顶栏也显示 6 秒，提交的 segments.seconds 却是 4
    //   （实测复刻：1 图 + 成片 6s → seconds: 4；3 图串联 → [4,4,4]）。
    const videoSeconds = filmSecondsFromChain(chain, nodes);
    for (const id of chain) {
      const node = byId.get(id);
      if (!node) continue;
      if (node.type === 'videoNode') {
        continue; // 秒数已由上面的全链预扫描取到，这里不再改（否则只影响它之后的段）
      }
      if (node.type === 'textNode') {
        const c = (node.data as TextNodeData).content.trim();
        if (c) texts.push(c);
      } else if (node.type === 'imageNode') {
        const img = node.data as ImageNodeData;
        if (!img.imageUrl.trim()) continue;
        const incoming = edges.find((e) => e.target === id);
        const textN = incoming ? byId.get(incoming.source) : undefined;
        // ★ 2026-09-19 修（#24）：**空的上游文本节点不能覆盖图节点自己的提示词**。
        //   原来只判类型（`textN.type === 'textNode'`）：上游挂着一个还没填内容的文本节点时，
        //   本段提示词被静默换成空串 ⇒ 该段视频走通用运镜兜底。
        //   用户看到的是「我明明在图节点写了提示词，生成的视频却跟我写的不一样」，且查不出原因。
        const promptFromText =
          textN && textN.type === 'textNode'
            ? (textN.data as TextNodeData).content.trim()
            : '';
        const prompt = promptFromText || img.prompt.trim();
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

  /**
   * 未匹配到场景锚定图的段数（仅在「确实配了场景锚」时才有意义）。
   *
   * 场景侧未命中时该段**不带任何场景参考图**（提交处 `fallback: 'none'`）——
   * 但「静默地没生效」恰是本项目最忌讳的状态（用户会以为锚定图在起作用），
   * 所以要在「生成成片」旁边把这几个段数说出来。
   */
  const scenesUnmatchedCount = useMemo(() => {
    if (Object.keys(effectiveSceneUrls).length === 0) return 0;
    return plan.segments.filter((s) => {
      const { picked } = pickUrlsByPrompt(s.prompt ?? '', effectiveSceneUrls, {
        fallback: 'none',
      });
      return Object.keys(picked).length === 0;
    }).length;
  }, [plan.segments, effectiveSceneUrls]);

  /**
   * 元素语义绑定的行：名词 → 锚定图（可灵式 `<Picture N>`，保证角色/道具跨镜一致）。
   *
   * ★ P2-9：编号**必须逐段现算**（`utils/segmentRefs.ts`，与提交时的组装共用同一个函数）。
   * 以前这里按**全局**编号（锚定图一律从 2 起、顺序 +1），而提交时每段只带这一段真正用到的
   * 锚定图（P0-3）→ 真实编号逐段不同。用真实画布 40 v16 实测的偏差：
   * 面板写「山村茅屋废墟 = 图片 7」，agent 实际写进提示词的是 `<Picture 4>`；
   * 面板写「小黑子 = 图片 4」，那一段其实根本没带它 —— 6 段里逐段都不一致。
   * 用户照面板配的编号去改提示词，就会指到不存在的图 / 指错对象。
   *
   * `pictureIndex` 只保留一个用途：画布上还没有可提交段落时的**预估位**（那时无编号可算）。
   */
  const bindingRows = useMemo(() => {
    const rows: {
      key: string;
      label: string;
      url: string;
      pictureIndex: number;
      indexes: number[];
    }[] = [];
    let idx = 2; // 预估位：Picture 1 是每段自己的图，锚定图从 2 开始
    const push = (key: string, label: string, url: string) => {
      rows.push({
        key,
        label,
        url,
        pictureIndex: idx++,
        // 各段真实编号（去重升序）；空数组 = 没有任何一段会带上它
        indexes: bindingIndexesInSegments(url, plan.segments, effectiveCharUrls, effectiveSceneUrls),
      });
    };
    for (const [name, ref] of Object.entries(effectiveCharRefs)) push(`char:${name}`, name, ref.url);
    for (const [name, ref] of Object.entries(effectiveSceneRefs)) push(`scene:${name}`, name, ref.url);
    return rows;
  }, [effectiveCharRefs, effectiveSceneRefs, plan.segments, effectiveCharUrls, effectiveSceneUrls]);

  // 生效的绑定行（名词非空 + **真的会被某一段带上**）。
  // 以前用「预估位 ≤ 5」当闸门，而那是全局编号：锚定图排在第 6 位时输入框被禁用、
  // 绑定永远发不出去 —— 可那一段其实会带上它（真实画布 40 的「山村茅草屋·午后」就是这种）。
  const activeBindingRows = bindingRows.filter(
    (r) => r.indexes.length > 0 && (bindingNames[r.key] ?? r.label).trim(),
  );

  /**
   * 新节点落点：优先「当前视口中心」。画布平移远了以后固定坐标会把新节点丢到视口外，
   * 用户看到的只是「点了没反应」；拿不到实例或尺寸时返回 null，调用方维持原有固定坐标。
   * 与已有节点重叠则逐个向下错开，避免连点几次叠成一摞只看得见一个。
   */
  const spawnPosition = useCallback(
    (nds: GraphNode[], size: { w: number; h: number }): { x: number; y: number } | null => {
      const inst = rfRef.current;
      // ⚠️ 用 .react-flow__pane 的 rect：页面在 <ReactFlow> 之外，拿不到容器 ref
      const pane = document.querySelector('.react-flow__pane')?.getBoundingClientRect();
      if (!inst || !pane || pane.width <= 0 || pane.height <= 0) return null;
      let pos: { x: number; y: number };
      try {
        const c = inst.screenToFlowPosition({
          x: pane.left + pane.width / 2,
          y: pane.top + pane.height / 2,
        });
        pos = { x: Math.round(c.x - size.w / 2), y: Math.round(c.y - size.h / 2) };
      } catch {
        return null; // 实例尚未就绪（首帧渲染前）
      }
      for (let guard = 0; guard < 20; guard += 1) {
        const hit = nds.some(
          (n) => Math.abs(n.position.x - pos.x) < 90 && Math.abs(n.position.y - pos.y) < 90,
        );
        if (!hit) break;
        pos = { ...pos, y: pos.y + size.h + 16 };
      }
      return pos;
    },
    [],
  );

  const addNode = useCallback(
    (type: GraphNode['type']) => {
      setNodes((nds) => {
        const offset = nds.length * 40;
        const base: GraphNode = {
          id: nextFreeNodeId(nds),
          type,
          position: spawnPosition(nds, { w: 264, h: 240 }) ?? { x: 60 + offset, y: 360 + offset },
          data: {} as never,
        };
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
    [setNodes, spawnPosition],
  );

  const addImageNode = useCallback(
    (
      url: string,
      candidates?: string[],
      /** 来源任务（从「历史作品/画布素材」入画布时带过来，只读追溯用） */
      origin?: { taskId: number; prompt: string },
    ) => {
      setNodes((nds) => [
        ...nds,
        {
          id: nextFreeNodeId(nds),
          type: 'imageNode',
          position:
            spawnPosition(nds, { w: 264, h: 240 }) ?? { x: 380, y: 420 + nds.length * 40 },
          data: {
            imageUrl: url,
            prompt: '',
            ratio: '16:9',
            // 候选一并带进节点：面板 12 格装不下每个任务的 3 张候选，
            // 而节点里本来就有「候选 N 张 · 点一张设为首帧」的切换器 ——
            // 不带的话用户在画布上永远只能拿到第 1 张（此时另一张可能才是好的）。
            ...(candidates && candidates.length > 1 ? { candidates } : {}),
            // 来源只记录、不写进 prompt：把 [角色锚]/[镜头] 那套静态设定塞进视频提示词
            // 会触发已知坑（列了谁就画谁、人数措辞凑人数），而图生视频要的是动作描述。
            // 用户点节点上的「填入提示词」才算显式接管（见 ImageNodeView）。
            ...(origin && origin.prompt.trim()
              ? { originTaskId: origin.taskId, originPrompt: origin.prompt }
              : {}),
          },
        },
      ]);
    },
    [setNodes, spawnPosition],
  );

  const onUploadAsset = async (file: File) => {
    try {
      const res = await uploadImage(file);
      addImageNode(res.url);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : '上传失败');
    }
  };

  // 素材来源：本画布素材（source=canvas_asset）/ 作品（source=work）分栏 + 分页。
  // 以前两者混排、只按 id 倒序取 12 格 —— 画布自己生成的素材会把格子占满，
  // 画廊里的作品一张也看不到（实测 12 格全是 canvas_asset，作品全在窗口外）。
  const [historyTab, setHistoryTab] = useState<'asset' | 'work'>('asset');
  const [historyPages, setHistoryPages] = useState(1);
  const {
    data: history,
    isLoading: historyLoading,
    isError: historyError,
    isFetching: historyFetching,
    refetch: refetchHistory,
  } = useQuery({
    // 分源/页数都进 key：切栏或加载更多就是另一条缓存，不必自己拼列表
    queryKey: [...HISTORY_IMAGES_KEY, historyTab, historyPages],
    queryFn: async () => {
      const res = await listTasks({
        page: 1,
        size: HISTORY_PAGE_SIZE * historyPages,
        genType: 'text_image',
        // ★ 后端过滤（以前是前端先取 40 条再筛 completed：排队/失败的任务会挤掉名额）
        status: 'completed',
        // ★ 后端按来源分区，不再靠「12 格里恰好有没有作品」
        source: historyTab,
        // 只为兼容「还没重启」的旧后端：旧 JVM 不认 source，若这里不传它会退回
        // includeAssets=false 的语义 → 连素材都查不到（两个栏都只剩作品）。
        // 新后端里 source 优先，这个参数不影响结果（见 sourceFilterOf 的单测）。
        includeAssets: true,
      });
      // 只留有图的任务：没图的任务在网格里渲染成空洞（占一格却点不动）
      // ⚠️ 刻意**不按提示词去重**：同镜重跑（同 prompt、各带自己的候选）是用户可能想
      //    分别挑的真实产物，且实测 32 条成品里精确重复只有 1 组（49/50），
      //    去重省下的格子是 0，代价却是丢掉一组候选 —— 不划算（2026-09-17 实测更正）。
      const rows = res.list.filter(
        (t) => t.status === 'completed' && parseImageUrls(t.imageUrls).length > 0,
      );
      return { rows, total: res.total };
    },
    staleTime: 30_000,
    // 加载更多/切栏时保留上一批：否则网格闪一次空态，看起来像「图丢了」
    placeholderData: (prev) => prev,
  });

  const mutation = useMutation({
    mutationFn: () => {
      // 提交前：把画布 state 的锚定图（优先）或 URL anchorRefs（兜底）合并到每个 segment 的 reference_images
      let segmentsJson: string | undefined;
      if (plan.segments.length > 0) {
        // 优先用画布 state（用户手动管理/从 URL 合并过），URL anchorRefs 作兜底。
        // ★ 每段只带**这一段真正用到**的锚定图（P0-3）：agnes 对每张参考图都加权，
        // 把无关角色/场景塞进去会被"拉"进画面；而且 5 张名额会被无关项占满，
        // 真正该出场的角色反被静默挤掉。角色侧未命中仍退回全给（保险），场景侧未命中不给
        // （宁缺勿错）。兜底口径、组装顺序与 5 张截断全部收在 `utils/segmentRefs.ts` ——
        // **元素绑定面板显示编号用的是同一个函数**，所以「面板写第几号」与「实际发第几号」
        // 不可能再分叉（P2-9）。
        const enriched = plan.segments.map((seg) => ({
          ...seg,
          reference_images: segmentRefImages(seg, effectiveCharUrls, effectiveSceneUrls),
        }));
        segmentsJson = JSON.stringify(enriched);
      }
      // ④ 元素语义绑定：名词 → <Picture N>（agent 转成占位符，保证角色/道具跨镜一致）
      // ★ P0-2：**带上 imageUrl**，让 agent 按「这张图在本段真实数组里的位置」现算编号
      //   —— 上面做了每段筛选（P0-3）后数组长度逐段不同，写死编号必然错位。
      //   `imageIndex` 仍在，作为 url 缺失时的兜底（agent 侧两者都认）。
      const referenceBindings =
        activeBindingRows.length > 0
          ? JSON.stringify(
              activeBindingRows.map((r) => ({
                name: (bindingNames[r.key] ?? r.label).trim(),
                // 编号只是 `imageUrl` 缺失时的兜底（agent 按 url 在各段真实数组里现算）；
                // 各段一致时用那个真实编号，逐段不同才退回预估位
                imageIndex: r.indexes.length === 1 ? r.indexes[0] : r.pictureIndex,
                imageUrl: r.url,
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
        // 首帧锁定 / 段间衔接（agent 侧 lock_first_frame 默认开，关掉必须显式传 false）
        lockFirstFrame,
        chainFrames,
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

  // URL ?autoImages=1：从小说页「一键直接生成」跳进来时，自动开跑一键文生图。
  // 守卫用 ref：HMR/重渲染/StrictMode 都不能让它跑第二遍（每张都是钱）。
  // 触发后立刻把参数从 URL 上摘掉——否则刷新页面会再跑一轮。
  const autoImagesRef = useRef(false);
  useEffect(() => {
    if (searchParams.get('autoImages') !== '1' || autoImagesRef.current) return;
    if (currentProjectId === null) return; // 等项目加载完再判断
    // 参数一旦"消费"就摘掉：否则用户之后手动加一个无图节点，会被这条 effect 误当成
    // 「刚跳进来」而自动开跑一轮出图（每张都是钱）。
    autoImagesRef.current = true;
    setSearchParams({ project: String(currentProjectId) }, { replace: true });
    if (pendingImageNodes.length === 0 || batchRunning) return; // 没有待生成节点：丢弃参数
    void runBatchTextToImage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, currentProjectId, pendingImageNodes.length, batchRunning]);

  const runBatchTextToImage = async () => {
    const targets = pendingImageNodes.map((n) => {
      const rawPrompt = ((n.data as ImageNodeData).prompt || '').trim();
      return {
        id: n.id,
        prompt: rawPrompt,
        // 画幅随节点走：不传 agnes 按 1:1 出图（实测正方形），与节点的 16:9 不一致
        ratio: (n.data as ImageNodeData).ratio || '16:9',
        // ★ 锚定图当参考图（2026-09-18 A/B 实测：场景侧明显收益、角色侧弱收益）。
        //   用**原始**提示词匹配 —— 见 startTextToImage 里「自证命中」的说明。
        refs: firstFrameRefsFor(rawPrompt, effectiveCharUrls, effectiveSceneUrls),
      };
    });
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
        // ★ P0-1：首帧提示词同样拼上命中的角色/场景**描述**
        //   （与单节点「文生图」共用 augmentPromptWithAnchors，同一套匹配口径）
        //   + 带上命中的锚定图当参考图（2026-09-18 A/B：场景侧明显收益）
        const urls = await generateOneImage(
          augmentPromptWithAnchors(t.prompt, effectiveCharRefs, effectiveSceneRefs),
          candidateCount,
          t.ratio,
          180_000,
          t.refs,
        );
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
    // 刚生成的素材要能立刻在「从历史作品选取」里选到。
    // 此前没有任何地方 invalidate 这个 key：批量跑完面板不更新，用户得刷新页面
    // 或切走再切回来才看得到新图（面板查询没有轮询，同页停留时不会自己 refetch）。
    queryClient.invalidateQueries({ queryKey: HISTORY_IMAGES_KEY });
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

  // === 存量画布提示词重算：按**当前** composer 规则重写 [角色锚] / [场景] / [镜头] ===
  // 画布里的提示词是**预处理时**合成的，改了 agent 的 composer 规则不会自动更新存量画布
  // （此前只能手跑技能目录里的脚本）。流程刻意分两步：
  //   ① dry-run（纯函数端点）→ 展示「将变 N 条 + 每条改了什么 + 改前改后」；
  //   ② 用户确认 → 用当前 version 走既有乐观锁 PUT → **回读确认**
  //      （PUT 回执里的 version 可能是 null，不算证据）。
  // 规则实现只有一处：agent 侧 import 真实 composer 函数，前端只展示与确认，不复制规则。
  const [recomposeOpen, setRecomposeOpen] = useState(false);
  const [recomposeBusy, setRecomposeBusy] = useState(false);
  const [recomposeResult, setRecomposeResult] = useState<RecomposeResult | null>(null);
  const [recomposeError, setRecomposeError] = useState('');
  const [recomposeDone, setRecomposeDone] = useState('');

  /** 可重算的节点：**带提示词**的图片节点（空提示词本来就会被端点判成结构异常，不必发过去） */
  const recomposeTargets = useMemo(
    () =>
      nodes
        .filter((n) => n.type === 'imageNode' && ((n.data as ImageNodeData).prompt || '').trim())
        .map((n) => ({ id: n.id, prompt: (n.data as ImageNodeData).prompt.trim() })),
    [nodes],
  );

  const runRecomposeDryRun = async () => {
    setRecomposeOpen(true);
    setRecomposeDone('');
    setRecomposeError('');
    if (recomposeTargets.length === 0) {
      setRecomposeResult(null);
      setRecomposeError('画布上还没有带提示词的图片节点，没有可重算的内容');
      return;
    }
    setRecomposeBusy(true);
    try {
      // analysis 传 null：画布的 analysisJson 不在 `/api/canvas` 的回包里（那是小说项目的字段），
      // 端点会退回关键词表判定动物，并在面板里**如实说明**用的是哪一种来源
      setRecomposeResult(await recomposePrompts(recomposeTargets, null));
    } catch (e) {
      setRecomposeResult(null);
      setRecomposeError(e instanceof Error ? e.message : '重算失败');
    } finally {
      setRecomposeBusy(false);
    }
  };

  /** 确认写入：落库走既有乐观锁 PUT，再回读确认（不看 PUT 回执） */
  const applyRecompose = async () => {
    if (!recomposeResult) return;
    if (currentProjectId === null) {
      setRecomposeError('请先用顶栏「保存」把画布存成一个项目，再落库重算结果');
      return;
    }
    const changed = new Map(
      recomposeResult.nodes.filter((r) => r.changed).map((r) => [r.id, r] as const),
    );
    if (changed.size === 0) return;

    const next = nodes.map((n) => {
      const r = changed.get(n.id);
      return r ? { ...n, data: { ...n.data, prompt: r.prompt } } : n;
    });
    const nodesJson = JSON.stringify({
      nodes: next.map(({ id, type, position, data }) => ({ id, type, position, data })),
    });
    const edgesJson = JSON.stringify(
      edges.map(({ id, source, target, markerEnd }) => {
        const e: Record<string, unknown> = { id, source, target };
        if (markerEnd) e.markerEnd = markerEnd;
        return e;
      }),
    );

    setRecomposeBusy(true);
    setRecomposeError('');
    setRecomposeDone('');
    try {
      const res = await saveProject(currentProjectId, {
        nodesJson,
        edgesJson,
        version: versionRef.current,
      });
      if (res.conflict) {
        // 并发保护：交给既有的二选一（用我的覆盖 / 载入服务端最新），不静默覆盖别人的改动
        resolveConflict(res, nodesJson, edgesJson);
        return;
      }
      versionRef.current = res.canvas?.version ?? versionRef.current + 1;
      lastSavedSnapshotRef.current = `${nodesJson}|${edgesJson}`;
      setNodes(next);

      // ★ 回读确认：PUT 回执里的 version 可能是 null，不能当成功证据
      const after = await getProject(currentProjectId);
      const parsed = JSON.parse(after.nodesJson ?? '[]');
      const rawNodes = (Array.isArray(parsed) ? parsed : (parsed.nodes ?? [])) as Array<{
        id?: string;
        data?: { prompt?: string };
      }>;
      const readBack = new Map<string, string>();
      for (const n of rawNodes) if (n.id) readBack.set(n.id, n.data?.prompt ?? '');
      const mismatch = [...changed.entries()]
        .filter(([id, r]) => readBack.get(id) !== r.prompt)
        .map(([id]) => id);
      versionRef.current = after.version ?? versionRef.current;
      setRecomposeDone(
        mismatch.length === 0
          ? `已写入画布并回读确认：${changed.size} 条提示词（画布版本 v${after.version ?? '?'}）`
          : `回读发现 ${mismatch.length} 条没写进去（${mismatch.join('、')}），请重试`,
      );
    } catch (e) {
      setRecomposeError(e instanceof Error ? e.message : '落库失败');
    } finally {
      setRecomposeBusy(false);
    }
  };

  // === 成片顺序写进图片节点（画布上显示「第 N 段」）===
  // chain 按 x 坐标排序 → 拖动节点即改顺序；不显示序号用户根本判断不出提交顺序。
  useEffect(() => {
    // ★ 2026-09-19 修（#26）：段号与"排顺序"必须分开算。
    //   - `place`：图片节点在 chain 里的位次（**所有**图片节点都有）→ 驱动 ▲▼ 与禁用边界
    //     （顺序由 x 坐标决定，拖动/▲▼ 对任何节点都有意义，没放图时也要能排）；
    //   - `order`：**会真正提交的**节点（有 imageUrl —— plan 里的
    //     `if (!img.imageUrl.trim()) continue;` 会跳过没图的）才有的「成片第几段」→ 驱动徽标。
    //   原来两者共用 order ⇒ 没放图的节点也显示「第 N 段」，而它根本不会被提交：
    //   徽标在说谎，用户按徽标判断顺序会点错对象（做「按段重生」时尤其明显）。
    const order = new Map<string, number>();
    const place = new Map<string, number>();
    let seq = 0;
    let placeSeq = 0;
    for (const id of chain) {
      const node = nodes.find((x) => x.id === id);
      if (node?.type !== 'imageNode') continue;
      place.set(id, (placeSeq += 1));
      if (((node.data as ImageNodeData).imageUrl || '').trim()) order.set(id, (seq += 1));
    }
    if (place.size === 0) return;
    // __orderTotal = 会提交的段数（成片段数）；__placeTotal = 图片节点总数（可排位数量）
    const total = order.size;
    const placeTotal = place.size;
    setNodes((nds) => {
      let changed = false;
      const next = nds.map((node) => {
        if (node.type !== 'imageNode') return node;
        const want = order.get(node.id) ?? 0;
        const wantPlace = place.get(node.id) ?? 0;
        const d = node.data as { __order?: number; __orderTotal?: number; __place?: number; __placeTotal?: number };
        if (d.__order === want && d.__orderTotal === total
            && d.__place === wantPlace && d.__placeTotal === placeTotal) return node;
        changed = true;
        return { ...node, data: { ...node.data, __order: want, __orderTotal: total,
                                  __place: wantPlace, __placeTotal: placeTotal } };
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
              {Object.entries(anchorCharRefs).map(([name, ref]) => (
                <div key={name} className={`mt-2 flex items-center gap-2 rounded border p-1.5 ${dark ? 'border-slate-700 bg-slate-900/40' : 'border-slate-200 bg-slate-50'}`}>
                  {/* 有候选时**新旧并排**（左=当前还没被覆盖的，右=新生成）：锚定图是全片基准，
                      覆盖掉就回不去，所以先给用户看、点了「用新图」才落库 */}
                  {anchorCandidate?.kind === 'char' && anchorCandidate.name === name ? (
                    <>
                      {brokenRefs[`anchorCur:${ref.url}`] ? (
                        <ThumbFailed
                          className="h-10 w-10 rounded object-cover opacity-40"
                          title="当前锚定图拿不到（链接可能已失效）—— 可改描述后点「重新生成」"
                        />
                      ) : (
                        <img src={cachedImageUrl(ref.url)} alt="当前" title="当前（尚未覆盖）" className="h-10 w-10 rounded object-cover opacity-40" onError={() => markRefBroken(`anchorCur:${ref.url}`)} />
                      )}
                      {brokenRefs[`anchorNew:${anchorCandidate.url}`] ? (
                        <ThumbFailed
                          className="h-10 w-10 rounded object-cover ring-2 ring-indigo-500"
                          title="新生成的锚定图拿不到（链接可能已失效）—— 可再点一次「重新生成」"
                        />
                      ) : (
                        <img src={cachedImageUrl(anchorCandidate.url)} alt="新生成" title="新生成" className="h-10 w-10 rounded object-cover ring-2 ring-indigo-500" onError={() => markRefBroken(`anchorNew:${anchorCandidate.url}`)} />
                      )}
                    </>
                  ) : brokenRefs[`anchor:${ref.url}`] ? (
                    <ThumbFailed title="这张锚定图拿不到（链接可能已失效）—— 可改描述后点「重新生成」，或重选这张" />
                  ) : (
                    <img src={cachedImageUrl(ref.url)} alt={name} className="h-10 w-10 rounded object-cover" onError={() => markRefBroken(`anchor:${ref.url}`)} />
                  )}
                  <div className="min-w-0 flex-1 truncate text-xs" title={ref.desc || undefined}>
                    {name}
                    {ref.desc ? <span className={`ml-1 ${dark ? 'text-slate-500' : 'text-slate-400'}`}>·{ref.desc.slice(0, 14)}…</span> : null}
                  </div>
                  {/* 超出 5 张上限的锚定图**不会参与生成** —— 以前这里毫无提示，
                      用户以为绑上了、实际被静默丢弃（P0-3 的可见性尾巴） */}
                  {(() => {
                    const row = bindingRows.find((r) => r.key === `char:${name}`);
                    // 判据是**事实**：各段都没带上它（提示词里没有它的名字 / 被 5 张上限截断）
                    const notSent = !!row && plan.segments.length > 0 && row.indexes.length === 0;
                    return notSent ? (
                      <span
                        className={`shrink-0 rounded px-1 text-[10px] ${dark ? 'bg-amber-900/50 text-amber-200' : 'bg-amber-100 text-amber-700'}`}
                        title={`每段最多带 ${MAX_REF_PICTURES} 张参考图（含每段自己的首帧图），而各段提示词里都没有它的名字 → 本片不会用到它`}
                      >
                        不会随段发送
                      </span>
                    ) : null;
                  })()}
                  {anchorCandidate?.kind === 'char' && anchorCandidate.name === name ? (
                    <>
                      <button
                        onClick={applyAnchorCandidate}
                        className="shrink-0 rounded bg-indigo-600 px-1.5 py-0.5 text-[10px] text-white hover:bg-indigo-500"
                      >
                        用新图
                      </button>
                      <button
                        onClick={() => setAnchorCandidate(null)}
                        className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${dark ? 'border-slate-600 text-slate-300 hover:bg-slate-800' : 'border-slate-300 text-slate-600 hover:bg-slate-100'}`}
                      >
                        保留旧图
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() =>
                        regenerateAnchor('char', name, descDrafts[`char:${name}`] ?? ref.desc ?? name)
                      }
                      title="用左边的描述重新生成（生成后先对比，不直接覆盖）"
                      disabled={regenerating?.kind === 'char' && regenerating?.name === name}
                      className={`shrink-0 text-xs ${dark ? 'text-slate-400 hover:text-blue-400' : 'text-slate-500 hover:text-blue-500'} disabled:cursor-wait disabled:opacity-40`}
                    >
                      <RefreshCw className={`h-4 w-4 ${regenerating?.kind === 'char' && regenerating?.name === name ? 'animate-spin' : ''}`} />
                    </button>
                  )}
                  <input
                    value={descDrafts[`char:${name}`] ?? ref.desc ?? name}
                    onChange={(e) => setDescDrafts((d) => ({ ...d, [`char:${name}`]: e.target.value }))}
                    placeholder="描述"
                    title="这段描述用于重新生成锚定图，也会拼进首帧文生图的提示词（改这里不影响已生成的图）"
                    className={`w-32 shrink-0 rounded border px-1 py-0.5 text-[10px] ${dark ? 'border-slate-600 bg-slate-800 text-slate-200' : 'border-slate-300 bg-white text-slate-700'}`}
                  />
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
              {Object.entries(anchorSceneRefs).map(([name, ref]) => (
                <div key={name} className={`mt-2 flex items-center gap-2 rounded border p-1.5 ${dark ? 'border-slate-700 bg-slate-900/40' : 'border-slate-200 bg-slate-50'}`}>
                  {anchorCandidate?.kind === 'scene' && anchorCandidate.name === name ? (
                    <>
                      {brokenRefs[`anchorCur:${ref.url}`] ? (
                        <ThumbFailed
                          className="h-10 w-10 rounded object-cover opacity-40"
                          title="当前锚定图拿不到（链接可能已失效）—— 可改描述后点「重新生成」"
                        />
                      ) : (
                        <img src={cachedImageUrl(ref.url)} alt="当前" title="当前（尚未覆盖）" className="h-10 w-10 rounded object-cover opacity-40" onError={() => markRefBroken(`anchorCur:${ref.url}`)} />
                      )}
                      {brokenRefs[`anchorNew:${anchorCandidate.url}`] ? (
                        <ThumbFailed
                          className="h-10 w-10 rounded object-cover ring-2 ring-indigo-500"
                          title="新生成的锚定图拿不到（链接可能已失效）—— 可再点一次「重新生成」"
                        />
                      ) : (
                        <img src={cachedImageUrl(anchorCandidate.url)} alt="新生成" title="新生成" className="h-10 w-10 rounded object-cover ring-2 ring-indigo-500" onError={() => markRefBroken(`anchorNew:${anchorCandidate.url}`)} />
                      )}
                    </>
                  ) : brokenRefs[`anchor:${ref.url}`] ? (
                    <ThumbFailed title="这张锚定图拿不到（链接可能已失效）—— 可改描述后点「重新生成」，或重选这张" />
                  ) : (
                    <img src={cachedImageUrl(ref.url)} alt={name} className="h-10 w-10 rounded object-cover" onError={() => markRefBroken(`anchor:${ref.url}`)} />
                  )}
                  <div className="min-w-0 flex-1 truncate text-xs" title={ref.desc || undefined}>
                    {name}
                    {ref.desc ? <span className={`ml-1 ${dark ? 'text-slate-500' : 'text-slate-400'}`}>·{ref.desc.slice(0, 14)}…</span> : null}
                  </div>
                  {(() => {
                    const row = bindingRows.find((r) => r.key === `scene:${name}`);
                    // 判据是**事实**：各段都没带上它（提示词里没有它的名字 / 被 5 张上限截断）
                    const notSent = !!row && plan.segments.length > 0 && row.indexes.length === 0;
                    return notSent ? (
                      <span
                        className={`shrink-0 rounded px-1 text-[10px] ${dark ? 'bg-amber-900/50 text-amber-200' : 'bg-amber-100 text-amber-700'}`}
                        title={`每段最多带 ${MAX_REF_PICTURES} 张参考图（含每段自己的首帧图），而各段提示词里都没有它的名字 → 本片不会用到它`}
                      >
                        不会随段发送
                      </span>
                    ) : null;
                  })()}
                  {anchorCandidate?.kind === 'scene' && anchorCandidate.name === name ? (
                    <>
                      <button
                        onClick={applyAnchorCandidate}
                        className="shrink-0 rounded bg-indigo-600 px-1.5 py-0.5 text-[10px] text-white hover:bg-indigo-500"
                      >
                        用新图
                      </button>
                      <button
                        onClick={() => setAnchorCandidate(null)}
                        className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${dark ? 'border-slate-600 text-slate-300 hover:bg-slate-800' : 'border-slate-300 text-slate-600 hover:bg-slate-100'}`}
                      >
                        保留旧图
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() =>
                        regenerateAnchor('scene', name, descDrafts[`scene:${name}`] ?? ref.desc ?? name)
                      }
                      title="用左边的描述重新生成（生成后先对比，不直接覆盖）"
                      disabled={regenerating?.kind === 'scene' && regenerating?.name === name}
                      className={`shrink-0 text-xs ${dark ? 'text-slate-400 hover:text-blue-400' : 'text-slate-500 hover:text-blue-500'} disabled:cursor-wait disabled:opacity-40`}
                    >
                      <RefreshCw className={`h-4 w-4 ${regenerating?.kind === 'scene' && regenerating?.name === name ? 'animate-spin' : ''}`} />
                    </button>
                  )}
                  <input
                    value={descDrafts[`scene:${name}`] ?? ref.desc ?? name}
                    onChange={(e) => setDescDrafts((d) => ({ ...d, [`scene:${name}`]: e.target.value }))}
                    placeholder="描述"
                    title="这段描述用于重新生成锚定图，也会拼进首帧文生图的提示词（改这里不影响已生成的图）"
                    className={`w-32 shrink-0 rounded border px-1 py-0.5 text-[10px] ${dark ? 'border-slate-600 bg-slate-800 text-slate-200' : 'border-slate-300 bg-white text-slate-700'}`}
                  />
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
            {/* 分源 chip：混排 + 只取 12 格时，画布自己生成的素材会把格子占满，
                画廊里的作品一张都看不到（实测 12 格全是 canvas_asset） */}
            <div className="mb-2 flex items-center gap-1">
              {HISTORY_TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => {
                    setHistoryTab(t.key);
                    // 换源回到第一页：否则新源会按上一源的页数一次取一大坨
                    setHistoryPages(1);
                  }}
                  className={`rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors ${
                    historyTab === t.key ? 'bg-violet-600 text-white shadow-sm' : `border ${theme.btn}`
                  }`}
                >
                  {t.label}
                </button>
              ))}
              {historyFetching && <span className={`text-[10px] ${theme.hint}`}>加载中…</span>}
            </div>
            <div className="grid grid-cols-3 gap-1.5">
              {(history?.rows ?? []).map((t) => {
                const urls = parseImageUrls(t.imageUrls);
                if (urls.length === 0) return null;
                return (
                  <button
                    key={t.id}
                    title={
                      '点击加入画布：' +
                      (t.prompt || `#${t.id}`) +
                      (urls.length > 1 ? `（该任务有 ${urls.length} 张候选，入画布后在节点里切换）` : '')
                    }
                    onClick={() =>
                      addImageNode(urls[0], urls, { taskId: t.id, prompt: t.prompt || '' })
                    }
                    className="group relative aspect-square overflow-hidden rounded-md border border-slate-400 hover:border-indigo-400"
                  >
                    {brokenRefs[`hist:${urls[0]}`] ? (
                      <span
                        className="flex h-full w-full items-center justify-center bg-slate-400/25 text-center text-[10px] leading-tight text-slate-500 dark:text-slate-300"
                        title="这件作品的图拿不到（产物可能已被清理）—— 先回画廊重新生成，或换一件"
                      >
                        图失效
                      </span>
                    ) : (
                      <img
                        src={cachedImageUrl(urls[0])}
                        alt={t.prompt || `任务 ${t.id}`}
                        className="h-full w-full object-cover"
                        loading="lazy"
                        onError={() => markRefBroken(`hist:${urls[0]}`)}
                      />
                    )}
                    {/* 候选数角标：候选已带进节点可在节点里切换，面板不提示用户就不知道 */}
                    {urls.length > 1 && (
                      <span className="absolute bottom-0 right-0 rounded-tl-md bg-black/60 px-1 text-[9px] font-medium text-white">
                        {urls.length} 张
                      </span>
                    )}
                  </button>
                );
              })}
              {/* 三态必须分开：加载中 / 加载失败都显示成「暂无历史作品」会让用户
                  以为作品丢了、跑去修错的地方（本项目反复踩过的「文案说谎」） */}
              {historyLoading && (
                <div className="col-span-3 py-4 text-center text-[11px] text-slate-500">
                  加载中…
                </div>
              )}
              {!historyLoading && historyError && (
                <div className="col-span-3 py-4 text-center text-[11px] text-amber-600">
                  历史作品加载失败{' '}
                  <button
                    type="button"
                    className="underline hover:text-amber-500"
                    onClick={() => refetchHistory()}
                  >
                    重试
                  </button>
                </div>
              )}
              {!historyLoading && !historyError && (history?.rows.length ?? 0) === 0 && (
                <div className="col-span-3 py-4 text-center text-[11px] text-slate-600">
                  {historyTab === 'asset'
                    ? '还没有画布素材：先在图片节点点「文生图」'
                    : '还没有作品（画布素材不在这里，切回「本画布素材」）'}
                </div>
              )}
            </div>
            {/* 加载更多：面板 12 格一页，作品一多就够不着了（此前是硬截断，没有入口）。
                顶到 48 张上限后不继续堆（窄侧栏不适合浏览全部），改为指向画廊 ——
                画廊有完整分页、筛选，以及「显示画布素材」开关（?assets=1 直接打开） */}
            {!historyLoading && !historyError && history && history.total > history.rows.length && (
              HISTORY_PAGE_SIZE * historyPages >= HISTORY_MAX_SIZE ? (
                <Link
                  to="/gallery?assets=1"
                  title={`面板最多展示 ${HISTORY_MAX_SIZE} 张；更多（共 ${history.total} 张）去画廊看`}
                  className={`mt-2 block w-full rounded-lg border px-2 py-1 text-center text-[11px] ${theme.btn}`}
                >
                  去画廊看全部（共 {history.total} 张）→
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() => setHistoryPages((p) => p + 1)}
                  disabled={historyFetching}
                  title="再加载一页"
                  className={`mt-2 w-full rounded-lg border px-2 py-1 text-[11px] disabled:opacity-50 ${theme.btn}`}
                >
                  加载更多（已显示 {history.rows.length} / 共 {history.total}）
                </button>
              )
            )}
          </section>
        </aside>

        {/* 画布 */}
        <main className="relative min-w-0 flex-1">
          {/* 锚定图 context：节点组件只收 {id,data}，页面状态只能这样传（见 AnchorsCtx 注释） */}
          <AnchorsCtx.Provider value={anchorsCtxValue}>
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
            // 存实例：新节点要落在当前视口中心（见 spawnPosition）——页面在 <ReactFlow> 之外，
            // 拿不到 ReactFlowProvider 的 context，只能在 onInit 里接一次
            onInit={(inst) => {
              rfRef.current = inst;
            }}
            fitView
            minZoom={0.15}
            maxZoom={2.5}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={24} size={1.5} color={theme.dots} />
            <Controls className="react-flow__controls" />
          </ReactFlow>
          </AnchorsCtx.Provider>

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
                    {/* 首帧锁定 / 段间衔接：agnes 的 keyframe 模式开关。
                        面板在画布外（pointer-events-auto 的悬浮栏），不需要 nodrag */}
                    <div className={`space-y-1.5 rounded-lg border p-2 text-[11px] ${theme.input}`}>
                      <label className="flex cursor-pointer items-start gap-2">
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={lockFirstFrame}
                          onChange={(e) => setLockFirstFrame(e.target.checked)}
                        />
                        <span>
                          <b>锁定首帧</b>
                          <span className={theme.hint}>
                            {' '}
                            · 视频从每段首帧图开始（keyframe）。
                            {lockFirstFrame
                              ? '已开启：锚定图/元素绑定不参与本段视频（官方禁止 keyframe 与参考图混用）'
                              : '不勾 = 首帧图只当参考图，模型可能重新构图，但锚定图会作为参考参与'}
                          </span>
                        </span>
                      </label>
                      <label className="flex cursor-pointer items-start gap-2">
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={chainFrames}
                          onChange={(e) => setChainFrames(e.target.checked)}
                        />
                        <span>
                          <b>段间衔接</b>
                          <span className={theme.hint}>
                            {' '}
                            · 用下一段首帧当本段尾帧。实测接缝跳变减半（2.58→1.40），
                            但会把本段结尾**拖向下一段的画面**：只适合「同一地点的一段连续动作」，
                            不同场景请保持关闭（否则本段内容会被改掉）
                          </span>
                        </span>
                      </label>
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
                        // 徽标即事实：直接用「各段真实编号」渲染，不再自己发明一个全局编号
                        const badge = bindingBadge(
                          row.indexes,
                          row.pictureIndex,
                          plan.segments.length > 0,
                        );
                        const notSent = badge.text === '未随段发送';
                        const overLimit = badge.overLimit;
                        return (
                          <div key={row.key} className="flex items-center gap-2">
                            {brokenRefs[`bind:${row.url}`] ? (
                              <ThumbFailed
                                className="h-9 w-9 rounded-md border border-slate-600"
                                title="这张参考图拿不到（链接可能已失效）—— 可重新生成该锚定图；画布上的绑定名不受影响"
                              />
                            ) : (
                              <img
                                src={cachedImageUrl(row.url)}
                                alt={row.label}
                                className="h-9 w-9 shrink-0 rounded-md border border-slate-600 object-cover"
                                onError={() => markRefBroken(`bind:${row.url}`)}
                              />
                            )}
                            <span
                              className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${
                                overLimit || notSent
                                  ? 'bg-amber-500/20 text-amber-500'
                                  : 'bg-indigo-500/20 text-indigo-400'
                              }`}
                              title={badge.title}
                            >
                              {badge.text}
                            </span>
                            <input
                              type="text"
                              value={bindingNames[row.key] ?? row.label}
                              onChange={(e) =>
                                setBindingNames((prev) => ({ ...prev, [row.key]: e.target.value }))
                              }
                              placeholder="剧本中的名词，如：我 / 破旧摩托车"
                              disabled={overLimit || notSent}
                              className={`min-w-0 flex-1 rounded-lg border px-2 py-1 text-[11px] outline-none disabled:opacity-40 ${theme.input}`}
                            />
                            {(overLimit || notSent) && (
                              <span className="shrink-0 text-[10px] text-amber-500">
                                {overLimit ? '超限' : '不发送'}
                              </span>
                            )}
                          </div>
                        );
                      })}
                      <p className={`pt-1 text-[10px] leading-relaxed ${theme.hint}`}>
                        图片 1 是每个片段自己的画面，逐段不同，因此不参与绑定。锚定图的编号是
                        **逐段现算**的（每段只带这一段用到的参考图）：各段一致时显示「图片 N」，
                        逐段不同时显示「逐段 …」，没有任何一段用到它就显示「未随段发送」。
                        agent 写进提示词的 &lt;Picture N&gt; 与这里显示的编号**同源**（同一个组装函数）。
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* 存量提示词重算面板：先预览（dry-run）→ 确认 → 乐观锁写入 → 回读确认 */}
              {recomposeOpen && (
                <div className={`pointer-events-auto w-[620px] max-w-[92vw] rounded-2xl border ${theme.bar} p-4 shadow-lg backdrop-blur`}>
                  <div className={`mb-2 flex items-center gap-1.5 text-[11px] font-semibold ${theme.headText}`}>
                    <Sparkles className="h-3.5 w-3.5" />
                    按当前规则重算提示词
                    <span className={`font-normal ${theme.hint}`}>
                      画布里的提示词是预处理时合成的；改过 agent 的合成规则后，这里按**现在的**规则重写一遍
                    </span>
                  </div>
                  {recomposeBusy && <p className={`text-[11px] ${theme.hint}`}>计算中…</p>}
                  {recomposeError && <p className="text-[11px] text-rose-500">{recomposeError}</p>}
                  {recomposeDone && <p className="text-[11px] text-emerald-500">{recomposeDone}</p>}
                  {recomposeResult && !recomposeBusy && (
                    <>
                      <p className={`text-[11px] ${theme.hint}`}>
                        将变 <b className={theme.headText}>{recomposeResult.changed_count}</b> 条 / 共{' '}
                        {recomposeResult.nodes.length} 条（动物判定来源：{recomposeResult.animal_source}
                        ）
                      </p>
                      <div className="mt-2 max-h-[240px] space-y-2 overflow-y-auto">
                        {recomposeResult.nodes.map((n) => {
                          const node = nodes.find((x) => x.id === n.id);
                          const beforePrompt = ((node?.data as ImageNodeData | undefined)?.prompt || '');
                          return (
                            <div
                              key={n.id}
                              className={`rounded-lg border p-2 text-[10px] leading-relaxed ${theme.input}`}
                            >
                              <div className="flex items-center gap-2">
                                <b>{n.id}</b>
                                {n.skipped ? (
                                  <span className="text-amber-500">跳过（结构不对，不硬改）</span>
                                ) : n.changed ? (
                                  <span className="text-indigo-400">将改动</span>
                                ) : (
                                  <span className={theme.hint}>无需改动</span>
                                )}
                              </div>
                              {n.changed && (
                                <div className="mt-1 space-y-0.5">
                                  {['[角色锚]', '[场景]', '[镜头]'].map((pfx) => {
                                    const a = blockOf(beforePrompt, pfx);
                                    const b2 = blockOf(n.prompt, pfx);
                                    if (a === b2) return null;
                                    return (
                                      <div key={pfx}>
                                        <span className={theme.hint}>{pfx} </span>
                                        <span className="text-rose-400 line-through">
                                          {shortText(a.replace(pfx, '')) || '（空）'}
                                        </span>
                                        <span className={theme.hint}> → </span>
                                        <span className="text-emerald-500">
                                          {shortText(b2.replace(pfx, '')) || '（空）'}
                                        </span>
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                              {n.reasons.length > 0 && (
                                <div className={`mt-1 ${theme.hint}`}>{n.reasons.join('；')}</div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                      <div className="mt-2 flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setRecomposeOpen(false)}
                          className={`rounded-lg border px-2.5 py-1 text-[11px] font-medium ${theme.btn}`}
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          onClick={() => void applyRecompose()}
                          disabled={recomposeBusy || recomposeResult.changed_count === 0}
                          className="rounded-lg bg-indigo-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
                        >
                          确认写入画布（{recomposeResult.changed_count} 条）
                        </button>
                      </div>
                    </>
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
              <button
                type="button"
                onClick={() => {
                  if (recomposeOpen) {
                    setRecomposeOpen(false);
                    return;
                  }
                  setControlPanelOpen(false);
                  setBindingPanelOpen(false);
                  void runRecomposeDryRun();
                }}
                title="按 agent 当前的合成规则重算画布上的提示词（先预览将变几条、每条改了什么，确认后才写入画布）"
                className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium ${theme.btn} ${recomposeOpen ? 'ring-1 ring-indigo-400' : ''}`}
              >
                <Sparkles className="h-3.5 w-3.5" /> 重算提示词
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
              {/* 场景锚未匹配要说出来：这几段不会带场景参考图（以前是「全给」→ 塞进别的场景） */}
              {scenesUnmatchedCount > 0 && (
                <span
                  className={`max-w-[190px] text-[10px] leading-tight ${theme.hint}`}
                  title="这些分镜的 [场景] 描述与任何场景锚定图的描述都对不上（措辞漂移），所以不会带场景参考图 —— 背景只能靠文字描述。想用上锚定图：把该段的 [场景] 文字改成与锚定图描述一致，或在锚定图面板重新生成这张。"
                >
                  <span className="text-amber-500">{scenesUnmatchedCount} 段</span>
                  未匹配到场景锚 · 不带场景参考图
                </span>
              )}
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