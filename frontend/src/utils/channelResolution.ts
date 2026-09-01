// Resolves a manufacturer's *preferred* outreach channel and buckets
// selections accordingly. This is the single source of truth for "which
// channel applies to this manufacturer" — Email/Call/Web Form eligibility
// must never be inferred purely from which contact fields happen to be
// populated (that was the bug this file fixes).
//
// Canonical preferred_channel values, verified directly against the live
// database (not assumed): "Email", "Phone", "Web Form", "HCP Portal",
// "Fax", "Other" — plus unset/null. Only the first three have any outreach
// mechanism in this app.

export type ResolvedChannel = "email" | "call" | "webform" | "unsupported";

interface ChannelFields {
  preferred_channel?: string | null;
  official_mi_email?: string | null;
  team_verified_email?: string | null;
  mi_phone?: string | null;
  mi_web_form_url?: string | null;
}

/** Exact-match (trimmed, case-insensitive) against the canonical values —
 *  not substring matching, so a future value like "No Web Presence" can
 *  never be mistaken for "Web Form". */
export function resolvePreferredChannel(mfr?: ChannelFields | null): ResolvedChannel {
  const v = (mfr?.preferred_channel || "").trim().toLowerCase();
  if (v === "email") return "email";
  if (v === "phone") return "call";
  if (v === "web form") return "webform";
  return "unsupported"; // "", "HCP Portal", "Fax", "Other", or anything unexpected
}

export function isEmailReachable(mfr?: ChannelFields | null): boolean {
  return !!(mfr?.official_mi_email || mfr?.team_verified_email);
}

export function isCallReachable(mfr?: ChannelFields | null): boolean {
  return !!mfr?.mi_phone;
}

export function isWebFormReachable(mfr?: ChannelFields | null): boolean {
  return !!mfr?.mi_web_form_url;
}

export interface ChannelBuckets<M> {
  /** Preferred channel matches AND the required contact field is present. */
  email: M[];
  call: M[];
  webform: M[];
  /** Preferred channel matches but the required contact field is missing —
   *  must be surfaced as "unreachable", never silently moved to another card. */
  emailUnreachable: M[];
  callUnreachable: M[];
  webformUnreachable: M[];
  /** No supported outreach mechanism for this preferred_channel value. */
  unsupported: M[];
}

export function bucketByPreferredChannel<M extends ChannelFields>(manufacturers: M[]): ChannelBuckets<M> {
  const buckets: ChannelBuckets<M> = {
    email: [], call: [], webform: [],
    emailUnreachable: [], callUnreachable: [], webformUnreachable: [],
    unsupported: [],
  };
  for (const m of manufacturers) {
    const resolved = resolvePreferredChannel(m);
    if (resolved === "email") {
      (isEmailReachable(m) ? buckets.email : buckets.emailUnreachable).push(m);
    } else if (resolved === "call") {
      (isCallReachable(m) ? buckets.call : buckets.callUnreachable).push(m);
    } else if (resolved === "webform") {
      (isWebFormReachable(m) ? buckets.webform : buckets.webformUnreachable).push(m);
    } else {
      buckets.unsupported.push(m);
    }
  }
  return buckets;
}
