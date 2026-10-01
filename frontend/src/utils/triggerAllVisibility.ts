/** Shown only when eligible manufacturers span at least two of the three
 * channel buckets (Email, Call, Web Form) — one bucket has nothing to combine. */
export function shouldShowTriggerAll(opts: {
  hasTriggerAllHandler: boolean;
  emailEligibleCount: number;
  callEligibleCount: number;
  webFormCapableCount: number;
  hasWebFormHandler: boolean;
}): boolean {
  if (!opts.hasTriggerAllHandler) return false;
  const channelsWithEligible = [
    opts.emailEligibleCount > 0,
    opts.callEligibleCount > 0,
    opts.hasWebFormHandler && opts.webFormCapableCount > 0,
  ].filter(Boolean).length;
  return channelsWithEligible >= 2;
}
