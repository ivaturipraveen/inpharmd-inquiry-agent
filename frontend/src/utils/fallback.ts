// Single canonical list of fallback-time choices. Every UI that lets a user
// pick a fallback duration (single-manufacturer Contact Manufacturer form,
// per-manufacturer rows in the same form, the Excel/MUE bulk form) must
// render from this list rather than defining its own options, so the set of
// choices stays identical everywhere in the app.
export const FALLBACK_PRESETS: { hours: number; label: string }[] = [
  { hours: 0, label: "5 min (testing)" },
  { hours: 12, label: "12 hours" },
  { hours: 24, label: "24 hours" },
  { hours: 48, label: "48 hours" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
];

export const DEFAULT_FALLBACK_HOURS = 24;

/** 0 is a sentinel value meaning "5 minutes" (for manual testing). */
export function fmtFallbackHours(h: number): string {
  if (h === 0) return "5 min";
  if (h <= 24) return `${h}h`;
  if (h % 24 === 0) return `${h / 24} days`;
  return `${h}h`;
}

/** Fallback column display: "Disabled" when the manufacturer has fallback
 * calling turned off, "Unavailable" when fallback is enabled but no MI phone
 * is on file (no call can be placed), otherwise the configured duration.
 * `enabled` is `undefined` when there's no matched manufacturer to check —
 * in that case we can't assert it's disabled, so fall back to showing the
 * duration. */
export function fmtFallbackStatus(
  enabled: boolean | undefined | null,
  h: number,
  miPhone?: string | null,
): string {
  if (enabled === false) return "Disabled";
  if (enabled === true && !miPhone) return "Unavailable";
  return fmtFallbackHours(h);
}

/** Fallback column display for a group of inquiries (e.g. one MUE row's
 * children). Shows the shared status only when every child's fallback
 * status is identical; otherwise "Varied" rather than picking one child
 * to represent the whole group. */
export function fmtFallbackGroup(
  items: { enabled: boolean | undefined | null; hours: number; miPhone?: string | null }[]
): string {
  if (items.length === 0) return "—";
  const statuses = items.map((it) => fmtFallbackStatus(it.enabled, it.hours, it.miPhone));
  return statuses.every((s) => s === statuses[0]) ? statuses[0] : "Varied";
}
