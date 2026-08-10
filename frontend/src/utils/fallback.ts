/** 0 is a sentinel value meaning "5 minutes" (for manual testing). */
export function fmtFallbackHours(h: number): string {
  if (h === 0) return "5 min";
  if (h < 24) return `${h}h`;
  if (h % 24 === 0) return `${h / 24} days`;
  return `${h}h`;
}
