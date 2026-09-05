// 从项目名/章节名中剥离章节标识，提取小说名（分组用）
// 例：
//   "长生烬-第一章" → "长生烬"
//   "长生烬 第一章 警花的恐惧" → "长生烬"
//   "长生烬01" → "长生烬"
// 剥离失败返回原名（保底）。
export function stripChapterSuffix(name: string): string {
  const trimmed = name.trim();
  const chapterPattern =
    /[-·\s]?(?:第\s*[\d一二三四五六七八九十百]+章|[\d一二三四五六七八九十]+)\s*[-·\s]*[^-\d一二三四五六七八九十\s]*$/;
  const stripped = trimmed.replace(chapterPattern, '').trim();
  return stripped || trimmed;
}
