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
}

export interface CanvasProjectRef {
  id: number;
  name: string;
  updatedAt?: string;
}
