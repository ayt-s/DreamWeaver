import { useCallback, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
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
} from 'lucide-react';
import {
  createVideoTask,
  getTask,
  listTasks,
  uploadImage,
} from '../api/tasks';
import { cachedImageUrl, parseImageUrls, type TaskResponse } from '../types/task';

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

      <div className="relative mb-2 flex h-36 items-center justify-center overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
        {data.imageUrl ? (
          <img
            src={cachedImageUrl(data.imageUrl)}
            alt="参考图"
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex flex-col items-center gap-1 text-slate-400">
            <Wand2 className="h-8 w-8" />
            <span className="text-[11px]">填提示词 → 一键文生图</span>
          </div>
        )}
      </div>

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
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const onConnect = useCallback(
    (conn: Connection) =>
      setEdges((eds) =>
        addEdge({ ...conn, markerEnd: { type: MarkerType.ArrowClosed, color: '#818cf8' } }, eds),
      ),
    [setEdges],
  );

  // 主链串行化：沿连线方向收集节点（分支会按拓扑序合并）
  const chain = useMemo(() => {
    const outgoing = new Map<string, string>();
    const incomingCount = new Map<string, number>();
    for (const e of edges) {
      outgoing.set(e.source, e.target);
      incomingCount.set(e.target, (incomingCount.get(e.target) ?? 0) + 1);
    }
    const entries = nodes.filter((n) => !incomingCount.has(n.id)).map((n) => n.id);
    const order: string[] = [];
    const seen = new Set<string>();
    for (const entry of entries) {
      let cur = entry;
      while (cur && !seen.has(cur)) {
        seen.add(cur);
        order.push(cur);
        cur = outgoing.get(cur) ?? '';
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
    mutationFn: () =>
      createVideoTask({
        prompt:
          plan.texts.join('；') ||
          (plan.segments.length > 0 ? '无限画布图生视频' : '无限画布'),
        genType: plan.segments.length > 0 ? 'image_video' : 'text_video',
        segments: plan.segments.length > 0 ? JSON.stringify(plan.segments) : undefined,
        videoModel: videoModel || undefined,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['tasks'] });
      navigate('/');
    },
  });

  const canSubmit = plan.segments.length > 0 || plan.texts.length > 0;
  const totalSeconds = plan.segments.length * plan.videoSeconds;

  return (
    <div className="flex h-screen flex-col bg-slate-950">
      {/* 顶栏 */}
      <header className="flex items-center gap-3 border-b border-slate-800 px-4 py-2.5">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-800"
        >
          <ArrowLeft className="h-4 w-4" /> 画廊
        </Link>
        <h1 className="text-sm font-semibold text-slate-100">无限画布 · 图生视频</h1>
        <span className="ml-2 hidden text-[11px] text-slate-500 xl:inline">
          拖拽节点自由摆放 · 滚轮缩放 · 空白处平移 · 连线决定生成顺序
        </span>
        <span className="ml-auto text-[11px] text-slate-500">
          {plan.segments.length > 0
            ? `${plan.segments.length} 段 · 每段 ${plan.videoSeconds}s · 约 ${totalSeconds}s`
            : '未接入图片，将按文本生成视频'}
        </span>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 左侧面板 */}
        <aside className="flex w-60 shrink-0 flex-col gap-4 overflow-y-auto border-r border-slate-800 p-3">
          <section>
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              添加节点
            </div>
            <div className="flex flex-col gap-1.5">
              <button
                onClick={() => addNode('textNode')}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"
              >
                <Type className="h-4 w-4 text-indigo-400" /> 文本节点
              </button>
              <button
                onClick={() => addNode('imageNode')}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"
              >
                <ImagePlus className="h-4 w-4 text-indigo-400" /> 图片节点
              </button>
              <button
                onClick={() => addNode('videoNode')}
                className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"
              >
                <Clapperboard className="h-4 w-4 text-indigo-400" /> 成片节点
              </button>
            </div>
          </section>

          <section>
            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              添加资源
            </div>
            <button
              onClick={() => document.getElementById('canvas-upload')?.click()}
              className="mb-2 inline-flex w-full items-center justify-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"
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
            <div className="mb-2 text-[11px] text-slate-500">从历史作品选取（点击入画布）：</div>
            <div className="grid grid-cols-3 gap-1.5">
              {(history ?? []).slice(0, 12).map((t) => {
                const urls = parseImageUrls(t.imageUrls);
                if (urls.length === 0) return null;
                return (
                  <button
                    key={t.id}
                    title={'点击加入画布：' + (t.prompt || `#${t.id}`)}
                    onClick={() => addImageNode(urls[0])}
                    className="group relative aspect-square overflow-hidden rounded-md border border-slate-700 hover:border-indigo-400"
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
            <Background variant={BackgroundVariant.Dots} gap={24} size={1.5} color="#1e293b" />
            <Controls className="react-flow__controls" />
          </ReactFlow>

          {/* 底部悬浮控制栏 */}
          <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center px-4">
            <div className="pointer-events-auto flex items-center gap-3 rounded-2xl border border-slate-700 bg-slate-900/90 px-4 py-2.5 shadow-lg backdrop-blur">
              <div>
                <div className="mb-0.5 text-[10px] text-slate-500">视频模型</div>
                <select
                  value={videoModel}
                  onChange={(e) => setVideoModel(e.target.value)}
                  className="rounded-lg border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 outline-none"
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
                onClick={() => mutation.mutate()}
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
    </div>
  );
}