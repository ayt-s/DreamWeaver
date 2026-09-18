/**
 * 候选主体计数：`POST /v1/qc/candidates`（agent-service，vite 已把 `/v1` 代理到 8000）。
 *
 * ## 为什么需要（项目的真实痛点）
 *
 * 已反复实测：同一提示词形态会**随机多出主体**（设定一头牛 → 出两头），
 * 而候选是同一 prompt 的**三次独立请求** → 三张会一起错，肉眼挑不出来。
 * 本地 OpenCV 只能判「面部特写」（YuNet），数不了主体。
 *
 * ## 标定（2026-09-18，13 张真实候选，逐张与我目视答案比对）
 *
 * **13/13 一致**：三张「两头牛」全数对 2、六张大脸特写全判 true。
 * ⚠️ 样本偏窄（同一部小说/画风），所以定位是**提示而不是结论** ——
 * 数错了只是徽标误导，不会自动淘汰候选。
 *
 * ## 与 `imageQc.ts` 的分工（刻意两个端点）
 *
 * - `imageQc` = 本地 YuNet 判大脸，确定性、快
 * - `candidateQc` = 多模态模型数主体，慢（串行，3 张约 15~25s）
 *
 * 所以角标是**晚到**的：候选缩略图先出来，数字随后补上。
 */
export type CandidateSubject = {
  index: number;
  url: string;
  skipped: boolean;
  reason?: string;
  people: number | null;
  animals: number | null;
  faceCloseup: boolean | null;
  /** 角标文案（agent 侧 `subject_label` 生成，如 `1人1牛`）—— 单一出处，别在前端再拼一份 */
  label: string;
};

export type CandidateSubjects = {
  results: CandidateSubject[];
  summary: { total: number; counted: number };
};

/** 一次最多数几张 —— 与 agent 侧 `MAX_CANDIDATES` 对齐，超了接口会 422。 */
export const MAX_CANDIDATE_QC = 6;

/**
 * 数一组候选的主体。**失败返回 null**（调用方静默降级：没有角标，不影响出图流程）——
 * 与 `qcImages` 同一约定：质检是附加信息。
 */
export async function countCandidates(urls: string[]): Promise<CandidateSubjects | null> {
  const list = urls.filter((u) => !!u).slice(0, MAX_CANDIDATE_QC);
  if (list.length === 0) return null;
  try {
    const res = await fetch('/v1/qc/candidates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: list }),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { code?: number; data?: CandidateSubjects };
    return body?.data ?? null;
  } catch {
    return null;
  }
}

/**
 * 找出「与其它候选不一样」的那些（**相对多数**，不是断言对错）。
 *
 * 为什么用相对多数：同一批候选的提示词/设定相同，所以多数派就是这一批的常态；
 * 单张的数字跟别张不同，才值得用户多看一眼（例如 3 张里 2 张是 `1人1牛`、
 * 1 张是 `1人2牛` → 后者标出来）。
 *
 * 三张全一样时返回空集 —— 那不是「都没问题」，只是**这批无法用相对比较**给出线索
 * （模型侧随机会让三张一起错，这时只能靠用户自己看）。
 */
export function outliersBySubjects(data: CandidateSubjects | null): Set<string> {
  const counted = (data?.results ?? []).filter((r) => !r.skipped && r.label);
  if (counted.length < 2) return new Set();
  const tally = new Map<string, number>();
  for (const r of counted) tally.set(r.label, (tally.get(r.label) ?? 0) + 1);
  if (tally.size < 2) return new Set();
  const majority = [...tally.entries()].sort((a, b) => b[1] - a[1])[0][0];
  return new Set(counted.filter((r) => r.label !== majority).map((r) => r.url));
}
