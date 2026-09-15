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
