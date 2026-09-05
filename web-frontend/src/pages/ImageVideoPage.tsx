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
} from '../api/canvas';
import { cachedImageUrl, parseImageUrls, type TaskResponse } from '../types/task';

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
}
interface VideoNodeData {
  seconds: number;
}

type GraphNode = Node<any>;

const RATIO_PRESETS = ['16:9', '9:16', '1:1', '4:3', '3:4'];
const TEXT_MODES = ['自己编写', '一句话生成剧本', '文生图', '文生视频', '图片反推提示词'];
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

const CANVAS_THEME_KEY = 'dreamweaver:canvas-theme';

const selectCls =
  'rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-700 outline-none focus:border-indigo-300';
const textareaCls =
  'w-full resize-none rounded-lg border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700 outline-none focus:border-indigo-300';

/* ------------------------------------------------------------------ */
/* 自定义节点组件（模块级定义，React Flow 要求 nodeTypes 静态稳定）        */
/* ------------------------------------------------------------------ */

function TextNodeView({ id, data }: NodeProps<GraphNode>) {
  const { updateNodeData } = useReactFlow();
  const patch = (p: Partial<TextNodeData>) => updateNodeData(id, p);
  return (
    <div className="w-60 rounded-xl border border-slate-200 bg-white p-3 shadow-md">
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !bg-indigo-400" />
      <div className="mb-2 flex items-center gap-1 text-[11px] font-semibold text-slate-500">
        <Type className="h-3.5 w-3.5" /> 文本节点
      </div>
      <select
        value={data.mode}
        onChange={(e) => patch({ mode: e.target.value })}
        className={selectCls + ' w-full'}
      >
        {TEXT_MODES.map((m) => (
          <option key={m} value={m} disabled={m !== '自己编写'}>
            {m === '自己编写' ? m : `${m}（规划中）`}
          </option>
        ))}
      </select>
      <textarea
        value={data.content}
        onChange={(e) => patch({ content: e.target.value })}
        rows={4}
        placeholder="描述画面内容，或输入一句提示词…"
        className={textareaCls + ' mt-2'}
      />
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !bg-indigo-400" />
    </div>
  );
}

function ImageNodeView({ id, data }: NodeProps<GraphNode>) {
  const { updateNodeData } = useReactFlow();
  const fileRef = useRef<HTMLInputElement>(null);
  const [generating, setGenerating] = useState(false);
  const [status, setStatus] = useState('');
  const patch = (p: Partial<ImageNodeData>) => updateNodeData(id, p);

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
      const res = await createVideoTask({ prompt, genType: 'text_image' });
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
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : '文生图失败');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="w-64 rounded-xl border border-indigo-200 bg-white p-3 shadow-md">
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !bg-indigo-400" />
      <div className="mb-2 flex items-center gap-1 text-[11px] font-semibold text-slate-500">
        <ImagePlus className="h-3.5 w-3.5" /> 图片节点
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
    <div className="w-52 rounded-xl border-2 border-indigo-500 bg-white p-3 shadow-md">
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

let nodeCounter = 0;
const nextId = () => `n${++nodeCounter}`;

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
  const [searchParams] = useSearchParams();
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
    await saveProject(id, {
      characterRefs: charJson,
      sceneRefs: sceneJson,
    });
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
      nodesJson: JSON.stringify({ theme: dark ? 'dark' : 'light', nodes: nodes.map(({ id, type, position, data }) => ({ id, type, position, data })) }),
      edgesJson,
    };
  }, [nodes, edges, dark]);

  // 删除当前项目：确认后服务端删除，本地清空为新建态
  const onDeleteProject = async () => {
    if (!currentProjectId) return;
    const p = projects.find((x) => x.id === currentProjectId);
    if (!p) return;
    if (!window.confirm(`删除项目「${p.name}」？删除后画布内容不可恢复。`)) return;
    try {
      await deleteProject(currentProjectId);
      setProjects((ps) => ps.filter((x) => x.id !== currentProjectId));
      setCurrentProjectId(null);
      setProjectName('');
      setNodes(initialNodes);
      setEdges([]);
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

  // 保存当前画布到当前项目（无项目先新建）
  const onSaveCanvas = async () => {
    const { nodesJson, edgesJson } = serializeCanvas();
    try {
      let id = currentProjectId;
      if (id === null) {
        const name = projectName.trim() || `画布 ${new Date().toLocaleTimeString()}`;
        const p = await createProject(name);
        id = p.id;
        setCurrentProjectId(p.id);
        setProjectName(p.name);
        setProjects((ps) => [...ps, p]);
      }
      await saveProject(id, {
        name: projectName.trim() || undefined,
        nodesJson,
        edgesJson,
      });
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
        if (parsed.theme === 'dark' || parsed.theme === 'light') setDark(parsed.theme === 'dark');
      } else {
        setNodes(initialNodes);
        setEdges(initialEdges);
        setDark(true);
      }
      setCurrentProjectId(p.id);
      setProjectName(p.name);
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
        });
      }
    }
    return { segments, texts, videoSeconds };
  }, [chain, nodes, edges]);

  const addNode = useCallback(
    (type: GraphNode['type']) => {
      setNodes((nds) => {
        const offset = nds.length * 40;
        const base: GraphNode = { id: nextId(), type, position: { x: 60 + offset, y: 360 + offset }, data: {} as never };
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
          id: nextId(),
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
      const { list } = await listTasks({ page: 1, size: 40, genType: 'text_image' });
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
        const charRefsMap = Object.keys(anchorCharRefs).length > 0 ? anchorCharRefs : (anchorRefs?.characters ?? {});
        const sceneRefsMap = Object.keys(anchorSceneRefs).length > 0 ? anchorSceneRefs : (anchorRefs?.scenes ?? {});
        const enriched = plan.segments.map((seg) => {
          const extraRefs: string[] = [];
          for (const url of Object.values(charRefsMap)) extraRefs.push(url);
          for (const url of Object.values(sceneRefsMap)) extraRefs.push(url);
          const merged = [seg.image_url, ...extraRefs].filter((u): u is string => !!u);
          return { ...seg, reference_images: merged.slice(0, 5) };
        });
        segmentsJson = JSON.stringify(enriched);
      }
      return createVideoTask({
        prompt:
          plan.texts.join('；') ||
          (plan.segments.length > 0 ? '无限画布图生视频' : '无限画布'),
        genType: plan.segments.length > 0 ? 'image_video' : 'text_video',
        segments: segmentsJson,
        videoModel: videoModel || undefined,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tasks'] });
      navigate('/');
    },
  });

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
            <Trash2 className="h-3.5 w-3.5" /> 删除
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
            nodeTypes={nodeTypes}
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
        </main>
      </div>
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