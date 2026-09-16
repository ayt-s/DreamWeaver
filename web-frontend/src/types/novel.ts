// 小说转漫剧类型定义（对齐 web-backend NovelSegment / NovelProjectResponse）

export interface NovelSegment {
  id: string;
  chapter: number;
  title: string;
  plot: string;
  characters: string[];
  scene: string;
  camera: string;
  seconds: number;
  mood: string;
  imagePrompt: string;
  videoPrompt: string;
  /** 结构化景别：远景/全景/中景/近景/特写（画布节点预填用；空 = 不指定） */
  shotSize?: string;
  /** 结构化机位：平视/俯拍/仰拍/航拍/过肩 */
  angle?: string;
  /** 结构化运镜：固定/推近/拉远/摇镜/移镜/跟拍/环绕 */
  movement?: string;
}

/** 分镜忠实度结论（后端 analysis_json.fidelity / 响应同名字段） */
export interface FidelityReport {
  /** false = 未通过（会提示用户先核对分镜）；null/undefined = 没跑（不提示） */
  passed?: boolean | null;
  reason?: string;
  missing?: string[];
  invented?: string[];
  attempts?: number;
}

export interface NovelProject {
  id: number;
  projectName: string;
  novelText: string;
  chaptersJson: string | null;
  analysisJson: string | null;
  segments: NovelSegment[];
  visualStyle: string | null;
  canvasProjectId: number | null;
  status: 'draft' | 'processing' | 'ready' | 'failed';
  errorMessage: string | null;
  /** 分镜忠实度结论（老数据为 null） */
  fidelity?: FidelityReport | null;
  createdAt: string;
  updatedAt: string;
}

export interface NovelPreprocessRequest {
  projectName: string;
  novelText: string;
  targetSegments: number; // 4-12，默认 6
  secondsPerSegment: number; // 4-12，默认 5
  /** 视觉风格短语；留空/不传 = 由 AI 通读原文后判断 */
  visualStyle?: string;
}

export interface CanvasProjectRef {
  id: number;
  name: string;
  updatedAt?: string;
}

/**
 * 「转入画布」结果。
 * needConfirm=true 表示目标画布已有内容且与本次结果不一致 —— 后端**没有写库**，
 * 需前端确认后带 force（覆盖）或 saveAsNew（另存为新画布）重发。
 */
export interface ToCanvasResult {
  canvas?: CanvasProjectRef | null;
  needConfirm: boolean;
  /** 以下仅在 needConfirm=true 时有值，用于生成确认文案 */
  canvasId?: number | null;
  canvasName?: string | null;
  canvasNodeCount?: number | null;
  canvasUpdatedAt?: string | null;
  incomingNodeCount?: number | null;
}
