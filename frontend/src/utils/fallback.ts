/** 0 is a sentinel value meaning "5 minutes" (for manual testing). */
export function fmtFallbackHours(h: number): string {
  if (h === 0) return "5 min";
  if (h <= 24) return `${h}h`;
  if (h % 24 === 0) return `${h / 24} days`;
  return `${h}h`;
}

/** Fallback column display: "Disabled" when the manufacturer has fallback
 * calling turned off, otherwise the configured duration. `enabled` is
 * `undefined` when there's no matched manufacturer to check — in that case
 * we can't assert it's disabled, so fall back to showing the duration. */
export function fmtFallbackStatus(enabled: boolean | undefined | null, h: number): string {
  if (enabled === false) return "Disabled";
  return fmtFallbackHours(h);
}

/** Fallback column display for a group of inquiries (e.g. one MUE row's
 * children). Shows the shared status only when every child's fallback
 * status is identical; otherwise "Varied" rather than picking one child
 * to represent the whole group. */
export function fmtFallbackGroup(
  items: { enabled: boolean | undefined | null; hours: number }[]
): string {
  if (items.length === 0) return "—";
  const statuses = items.map((it) => fmtFallbackStatus(it.enabled, it.hours));
  return statuses.every((s) => s === statuses[0]) ? statuses[0] : "Varied";
}
