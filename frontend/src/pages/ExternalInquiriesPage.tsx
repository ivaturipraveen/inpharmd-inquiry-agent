import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiMeta } from "../api";

// Staging returns JSON:API with hyphenated attribute keys:
//   {data: [{id, type, attributes: {question, status, "submitter-email",
//                                    "created-at", "all-documents", …}}]}
// Detail returns a different shape:
//   {inquiry_uuid, title, submitter_id, submitter_details: {…}}

type Json = Record<string, any>;

const pick = (row: Json | null | undefined, ...keys: string[]): any => {
  if (!row) return undefined;
  const sources: Json[] = [row, row.attributes ?? {}];
  for (const src of sources) {
    for (const k of keys) {
      const variants = [k, k.replace(/-/g, "_"), k.replace(/_/g, "-")];
      for (const v of variants) {
        if (src[v] !== undefined && src[v] !== null && src[v] !== "") return src[v];
      }
    }
  }
  return undefined;
};

const fmtTimestamp = (v: any): string => {
  if (v == null || v === "") return "—";
  if (typeof v === "number") {
    const d = new Date(v * 1000);
    return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString();
  }
  const d = new Date(v);
  return isNaN(d.getTime()) ? String(v) : d.toLocaleDateString();
};

const fmtDateTime = (v: any): string => {
  if (v == null || v === "") return "—";
  if (typeof v === "number") {
    const d = new Date(v * 1000);
    return isNaN(d.getTime()) ? String(v) : d.toLocaleString();
  }
  const d = new Date(v);
  return isNaN(d.getTime()) ? String(v) : d.toLocaleString();
};

const fmtRelative = (v: any): string => {
  if (!v) return "—";
  const seconds = typeof v === "number" ? v : Math.floor(new Date(v).getTime() / 1000);
  if (!seconds || isNaN(seconds)) return "—";
  const ageSec = Math.floor(Date.now() / 1000) - seconds;
  if (ageSec < 60) return "just now";
  if (ageSec < 3600) return `${Math.floor(ageSec / 60)}m ago`;
  if (ageSec < 86400) return `${Math.floor(ageSec / 3600)}h ago`;
  if (ageSec < 86400 * 30) return `${Math.floor(ageSec / 86400)}d ago`;
  if (ageSec < 86400 * 365) return `${Math.floor(ageSec / (86400 * 30))}mo ago`;
  return `${Math.floor(ageSec / (86400 * 365))}y ago`;
};

const truncate = (s: any, n = 120): string => {
  const str = String(s ?? "");
  return str.length <= n ? str : str.slice(0, n) + "…";
};

const prettyStatus = (s: string): string =>
  s
    .split(/[_\s-]+/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

// Map staging statuses → traffic-light groups so we can colour them
// consistently across the page.
const STATUS_GROUPS: Record<string, "good" | "warn" | "info" | "neutral" | "bad"> = {
  complete: "good",
  approved: "good",
  published: "good",
  response_formulation: "info",
  literature_search: "warn",
  review: "info",
  reopened: "warn",
  cancelled: "bad",
  rejected: "bad",
};

const statusColor = (status: string): string => {
  return STATUS_GROUPS[status.toLowerCase()] ?? "neutral";
};

const TURNAROUND_RANK: Record<string, number> = {
  asap: 0,
  one_day: 1,
  a_week: 2,
  not_urgent: 3,
};

const turnaroundLabel = (t: string): string => {
  switch (t) {
    case "asap":
      return "ASAP";
    case "one_day":
      return "1 day";
    case "a_week":
      return "1 week";
    case "not_urgent":
      return "Not urgent";
    default:
      return prettyStatus(t);
  }
};

const turnaroundClass = (t: string): string => {
  if (t === "asap") return "ta-asap";
  if (t === "one_day") return "ta-soon";
  if (t === "a_week") return "ta-week";
  return "ta-low";
};

const SOFT_SECRETS = new Set([
  "password_digest",
  "password_reset_token",
  "password_reset_sent_at",
  "one_time_password",
  "one_time_password_sent_at",
  "one_time_password_expires_at",
  "remember_me_token",
  "account_activation_token",
  "magic_invite_code",
]);

const redact = (obj: any): any => {
  if (Array.isArray(obj)) return obj.map(redact);
  if (obj && typeof obj === "object") {
    const out: Json = {};
    for (const [k, v] of Object.entries(obj)) {
      out[k] = SOFT_SECRETS.has(k) ? "[redacted in UI]" : redact(v);
    }
    return out;
  }
  return obj;
};

// Group statuses into "Open" vs "Completed" buckets for the headline stats.
const OPEN_STATUSES = new Set([
  "literature_search",
  "response_formulation",
  "review",
  "reopened",
]);
const CLOSED_STATUSES = new Set(["complete", "approved", "published"]);

type SortKey = "newest" | "oldest" | "urgency" | "status";

export default function ExternalInquiriesPage() {
  const [raw, setRaw] = useState<any>(null);
  const [meta, setMeta] = useState<ApiMeta | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [turnaroundFilter, setTurnaroundFilter] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [withDocsOnly, setWithDocsOnly] = useState(false);
  const [sort, setSort] = useState<SortKey>("newest");
  const [selected, setSelected] = useState<Json | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async (forceFresh = false) => {
    setLoading(true);
    setError(null);
    try {
      const { data, meta } = await api.externalInquiries.list({ fresh: forceFresh });
      setRaw(data);
      setMeta(meta);
      setLastLoadedAt(Date.now());
    } catch (err: any) {
      setError(err?.message ?? "Failed to load inquiries from InpharmD.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  // Tick once per minute so the "loaded N min ago" label stays current
  // without forcing a re-fetch.
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const inquiries: Json[] = useMemo(() => {
    if (!raw) return [];
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw.data)) return raw.data;
    if (Array.isArray(raw.inquiries)) return raw.inquiries;
    if (Array.isArray(raw.results)) return raw.results;
    return [];
  }, [raw]);

  // ----- Stats -----
  const stats = useMemo(() => {
    const byStatus: Record<string, number> = {};
    const byTurnaround: Record<string, number> = {};
    const byAssignee: Record<string, number> = {};
    let open = 0,
      closed = 0,
      inReview = 0,
      unread = 0,
      withDocs = 0,
      urgent = 0,
      thisWeek = 0;
    const now = Math.floor(Date.now() / 1000);
    const weekAgo = now - 7 * 86400;

    for (const i of inquiries) {
      const status = String(pick(i, "status") ?? "unknown");
      byStatus[status] = (byStatus[status] || 0) + 1;
      if (OPEN_STATUSES.has(status)) open++;
      if (CLOSED_STATUSES.has(status)) closed++;
      if (status === "review") inReview++;

      const ta = String(pick(i, "turnaround-time") ?? "unknown");
      byTurnaround[ta] = (byTurnaround[ta] || 0) + 1;
      if (ta === "asap" || ta === "one_day") urgent++;

      const assignee = String(pick(i, "assignee") ?? "").trim();
      if (assignee) byAssignee[assignee] = (byAssignee[assignee] || 0) + 1;

      if (pick(i, "is-unread")) unread++;
      const docs = pick(i, "all-documents");
      if (Array.isArray(docs) && docs.length > 0) withDocs++;

      const created = Number(pick(i, "created-at") ?? 0);
      if (created >= weekAgo) thisWeek++;
    }

    return {
      total: inquiries.length,
      open,
      closed,
      inReview,
      unread,
      withDocs,
      urgent,
      thisWeek,
      byStatus,
      byTurnaround,
      topAssignees: Object.entries(byAssignee)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5),
    };
  }, [inquiries]);

  // ----- Filter + sort -----
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const out = inquiries.filter((i) => {
      if (statusFilter && String(pick(i, "status") ?? "") !== statusFilter) return false;
      if (turnaroundFilter && String(pick(i, "turnaround-time") ?? "") !== turnaroundFilter) return false;
      if (unreadOnly && !pick(i, "is-unread")) return false;
      if (withDocsOnly) {
        const docs = pick(i, "all-documents");
        if (!Array.isArray(docs) || docs.length === 0) return false;
      }
      if (q) {
        const haystack = [
          pick(i, "question"),
          pick(i, "title"),
          pick(i, "submitter"),
          pick(i, "submitter-email"),
          pick(i, "assignee"),
          pick(i, "reviewer"),
          pick(i, "category"),
          pick(i, "status"),
          String(pick(i, "id") ?? ""),
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });

    out.sort((a, b) => {
      if (sort === "newest" || sort === "oldest") {
        const av = Number(pick(a, "created-at") ?? 0);
        const bv = Number(pick(b, "created-at") ?? 0);
        return sort === "newest" ? bv - av : av - bv;
      }
      if (sort === "urgency") {
        const av = TURNAROUND_RANK[String(pick(a, "turnaround-time") ?? "")] ?? 99;
        const bv = TURNAROUND_RANK[String(pick(b, "turnaround-time") ?? "")] ?? 99;
        if (av !== bv) return av - bv;
        return Number(pick(b, "created-at") ?? 0) - Number(pick(a, "created-at") ?? 0);
      }
      // status — group same statuses together, then newest first within
      const av = String(pick(a, "status") ?? "");
      const bv = String(pick(b, "status") ?? "");
      if (av !== bv) return av.localeCompare(bv);
      return Number(pick(b, "created-at") ?? 0) - Number(pick(a, "created-at") ?? 0);
    });
    return out;
  }, [inquiries, search, statusFilter, turnaroundFilter, unreadOnly, withDocsOnly, sort]);

  const openDetail = async (row: Json) => {
    const id = pick(row, "id", "uuid", "inquiry_id");
    setSelected(row);
    setSelectedDetail(null);
    setDetailError(null);
    if (id == null) {
      setDetailError("This row has no id we can use to fetch details.");
      return;
    }
    setDetailLoading(true);
    try {
      const { data } = await api.externalInquiries.get(id);
      setSelectedDetail(data);
    } catch (err: any) {
      setDetailError(err?.message ?? "Failed to load detail.");
    } finally {
      setDetailLoading(false);
    }
  };

  const closeModal = () => {
    setSelected(null);
    setSelectedDetail(null);
    setDetailError(null);
  };

  const allStatuses = useMemo(
    () => Object.entries(stats.byStatus).sort((a, b) => b[1] - a[1]),
    [stats.byStatus]
  );
  const allTurnarounds = useMemo(
    () => Object.entries(stats.byTurnaround).sort((a, b) => b[1] - a[1]),
    [stats.byTurnaround]
  );

  return (
    <>
      <section className="page-head">
        <h1>InpharmD Inquiries</h1>
        <p>
          Live feed from the InpharmD platform. {stats.total.toLocaleString()} total inquiries —
          {" "}<strong>{stats.open}</strong> open,{" "}
          <strong>{stats.closed}</strong> completed,{" "}
          <strong>{stats.unread}</strong> unread.
        </p>
        <CacheBadge meta={meta} loadedAt={lastLoadedAt} />
      </section>

      {/* Headline stats — at-a-glance KPIs */}
      <div className="ext-stats">
        <StatTile label="Total"     value={stats.total}     tone="neutral" icon="list" />
        <StatTile label="Open"      value={stats.open}      tone="warn"    icon="clock"
                  sub="Lit search + formulation + review" />
        <StatTile label="Completed" value={stats.closed}    tone="good"    icon="check" />
        <StatTile label="In Review" value={stats.inReview}  tone="info"    icon="eye" />
        <StatTile label="Unread"    value={stats.unread}    tone="info"    icon="dot" />
        <StatTile label="Urgent"    value={stats.urgent}    tone="bad"     icon="bolt"
                  sub="ASAP + 1-day" />
        <StatTile label="With Docs" value={stats.withDocs}  tone="neutral" icon="paperclip" />
        <StatTile label="New (7d)"  value={stats.thisWeek}  tone="info"    icon="sparkle" />
      </div>

      {/* Status breakdown — clickable chips, doubles as filter */}
      <div className="ext-section">
        <div className="ext-section-head">
          <h3>By status</h3>
          {statusFilter && (
            <button type="button" className="ext-clear" onClick={() => setStatusFilter("")}>
              Clear filter
            </button>
          )}
        </div>
        <div className="ext-chips">
          <button
            type="button"
            className={`ext-chip ${!statusFilter ? "ext-chip-active" : ""}`}
            onClick={() => setStatusFilter("")}
          >
            All <span className="ext-chip-num">{stats.total}</span>
          </button>
          {allStatuses.map(([s, n]) => (
            <button
              key={s}
              type="button"
              className={`ext-chip ext-chip-${statusColor(s)} ${
                statusFilter === s ? "ext-chip-active" : ""
              }`}
              onClick={() => setStatusFilter(statusFilter === s ? "" : s)}
            >
              {prettyStatus(s)} <span className="ext-chip-num">{n}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Turnaround breakdown */}
      <div className="ext-section">
        <div className="ext-section-head">
          <h3>By turnaround</h3>
          {turnaroundFilter && (
            <button type="button" className="ext-clear" onClick={() => setTurnaroundFilter("")}>
              Clear filter
            </button>
          )}
        </div>
        <div className="ext-chips">
          {allTurnarounds.map(([t, n]) => (
            <button
              key={t}
              type="button"
              className={`ext-chip ${turnaroundClass(t)} ${
                turnaroundFilter === t ? "ext-chip-active" : ""
              }`}
              onClick={() => setTurnaroundFilter(turnaroundFilter === t ? "" : t)}
            >
              {turnaroundLabel(t)} <span className="ext-chip-num">{n}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Search + sort + checkboxes */}
      <div className="filter-bar">
        <div className="filter-row">
          <div className="search-wrap">
            <svg
              className="search-icon"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="9" cy="9" r="6" />
              <path d="m17 17-3.5-3.5" />
            </svg>
            <input
              className="search-input"
              placeholder="Search question, submitter, assignee, id…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className="filter-select"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            title="Sort"
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="urgency">Urgency (ASAP first)</option>
            <option value="status">By status</option>
          </select>
          <label className="ext-toggle">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.target.checked)}
            />
            Unread only
          </label>
          <label className="ext-toggle">
            <input
              type="checkbox"
              checked={withDocsOnly}
              onChange={(e) => setWithDocsOnly(e.target.checked)}
            />
            With docs only
          </label>
          <div className="filter-spacer" />
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => load(true)}
            title="Force a fresh fetch from staging (bypass cache)"
          >
            Refresh
          </button>
        </div>
        <div className="filter-meta">
          Showing <strong>{filtered.length.toLocaleString()}</strong> of{" "}
          {stats.total.toLocaleString()} inquiries
          {(statusFilter || turnaroundFilter || unreadOnly || withDocsOnly || search) &&
            " (filtered)"}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="table-card">
        {loading ? (
          <div className="empty">
            <div className="empty-title">Loading from InpharmD…</div>
            <div className="empty-sub">List response is a few MB — give it a moment.</div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty">
            <div className="empty-title">No inquiries match</div>
            <div className="empty-sub">Try clearing your filters above.</div>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="data-table ext-table">
              <colgroup>
                <col className="col-id" />
                <col className="col-question" />
                <col className="col-submitter" />
                <col className="col-assignee" />
                <col className="col-status" />
                <col className="col-turnaround" />
                <col className="col-docs" />
                <col className="col-age" />
              </colgroup>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Question</th>
                  <th>Submitter</th>
                  <th>Assignee</th>
                  <th>Status</th>
                  <th>Turnaround</th>
                  <th className="th-center" title="Documents">
                    📎
                  </th>
                  <th>Age</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((i, idx) => {
                  const id = pick(i, "id") ?? `?`;
                  const question = pick(i, "question") ?? pick(i, "title") ?? "(no question)";
                  const submitterName = pick(i, "submitter") ?? pick(i, "submitter-email") ?? "—";
                  const submitterEmail = pick(i, "submitter-email") ?? "";
                  const assignee = pick(i, "assignee") ?? "—";
                  const status = String(pick(i, "status") ?? "unknown");
                  const ta = String(pick(i, "turnaround-time") ?? "");
                  const docs = pick(i, "all-documents");
                  const docCount = Array.isArray(docs) ? docs.length : 0;
                  const created = pick(i, "created-at");
                  const isUnread = !!pick(i, "is-unread");
                  return (
                    <tr
                      key={String(id) + "-" + idx}
                      onClick={() => openDetail(i)}
                      className={isUnread ? "ext-row-unread" : ""}
                    >
                      <td className="ext-id-cell">
                        {isUnread && <span className="ext-unread-dot" aria-label="Unread" />}
                        <span className="ext-id">#{String(id)}</span>
                      </td>
                      <td className="ext-question-cell">
                        <div className="ext-question-text" title={String(question)}>
                          {String(question)}
                        </div>
                      </td>
                      <td className="ext-submitter-cell">
                        <div className="ext-submitter-name">{String(submitterName)}</div>
                        {submitterEmail && submitterEmail !== submitterName && (
                          <div className="ext-submitter-email">{String(submitterEmail)}</div>
                        )}
                      </td>
                      <td className="cell-muted ext-nowrap">{String(assignee)}</td>
                      <td className="ext-pill-cell">
                        <span className={`status-badge status-${statusColor(status)}`}>
                          {prettyStatus(status)}
                        </span>
                      </td>
                      <td className="ext-pill-cell">
                        {ta ? (
                          <span className={`ext-ta-pill ${turnaroundClass(ta)}`}>
                            {turnaroundLabel(ta)}
                          </span>
                        ) : (
                          <span className="cell-muted">—</span>
                        )}
                      </td>
                      <td className="ext-docs-cell">
                        {docCount > 0 ? (
                          <span className="ext-doc-badge" title={`${docCount} attachment(s)`}>
                            {docCount}
                          </span>
                        ) : (
                          <span className="cell-muted">·</span>
                        )}
                      </td>
                      <td className="cell-muted ext-nowrap" title={fmtDateTime(created)}>
                        {fmtRelative(created)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selected && (
        <DetailModal
          inquiry={selected}
          detail={selectedDetail}
          detailLoading={detailLoading}
          detailError={detailError}
          onClose={closeModal}
        />
      )}
    </>
  );
}

// ───────────────────────── Cache badge ─────────────────────────

const fmtAge = (seconds: number): string => {
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
};

interface CacheBadgeProps {
  meta: ApiMeta | null;
  loadedAt: number | null;
}

const CacheBadge = ({ meta, loadedAt }: CacheBadgeProps) => {
  if (!meta || !loadedAt) return null;
  const loadedAge = Math.floor((Date.now() - loadedAt) / 1000);

  if (meta.cache === "STALE") {
    return (
      <div className="cache-badge cache-stale">
        <span className="cache-dot cache-dot-stale" />
        <strong>Showing stale cache</strong>
        {meta.cacheAgeSeconds != null && (
          <> · cached {fmtAge(meta.cacheAgeSeconds)}</>
        )}
        {meta.upstreamError && (
          <> · upstream: <code>{meta.upstreamError}</code></>
        )}
      </div>
    );
  }
  if (meta.cache === "HIT") {
    return (
      <div className="cache-badge cache-hit">
        <span className="cache-dot cache-dot-hit" />
        Cached
        {meta.cacheAgeSeconds != null && <> · {fmtAge(meta.cacheAgeSeconds)}</>}
        {loadedAt && <> · viewed {fmtAge(loadedAge)}</>}
      </div>
    );
  }
  if (meta.cache === "MISS") {
    return (
      <div className="cache-badge cache-fresh">
        <span className="cache-dot cache-dot-fresh" />
        Fresh from InpharmD
        {loadedAt && <> · {fmtAge(loadedAge)}</>}
      </div>
    );
  }
  return null;
};

// ─────────────────────────── Stat tile ───────────────────────────

type IconKey =
  | "list"
  | "clock"
  | "check"
  | "eye"
  | "dot"
  | "bolt"
  | "paperclip"
  | "sparkle";

interface StatTileProps {
  label: string;
  value: number;
  sub?: string;
  tone: "good" | "warn" | "bad" | "info" | "neutral";
  icon: IconKey;
}

const StatTile = ({ label, value, sub, tone, icon }: StatTileProps) => (
  <div className={`ext-stat ext-stat-${tone}`}>
    <div className="ext-stat-head">
      <div className="ext-stat-label">{label}</div>
      <div className="ext-stat-icon" aria-hidden>
        <StatIcon name={icon} />
      </div>
    </div>
    <div className="ext-stat-value">{value.toLocaleString()}</div>
    {sub && <div className="ext-stat-sub">{sub}</div>}
  </div>
);

const StatIcon = ({ name }: { name: IconKey }) => {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "list":
      return (
        <svg {...common}>
          <line x1="8" y1="6" x2="20" y2="6" />
          <line x1="8" y1="12" x2="20" y2="12" />
          <line x1="8" y1="18" x2="20" y2="18" />
          <circle cx="4" cy="6" r="1" />
          <circle cx="4" cy="12" r="1" />
          <circle cx="4" cy="18" r="1" />
        </svg>
      );
    case "clock":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="9" />
          <polyline points="12 7 12 12 15.5 14" />
        </svg>
      );
    case "check":
      return (
        <svg {...common}>
          <path d="M20 7L9 18l-5-5" />
        </svg>
      );
    case "eye":
      return (
        <svg {...common}>
          <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
    case "dot":
      return (
        <svg {...common}>
          <circle cx="18" cy="6" r="3" fill="currentColor" />
          <path d="M21 12.5V18a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3h5.5" />
        </svg>
      );
    case "bolt":
      return (
        <svg {...common}>
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
        </svg>
      );
    case "paperclip":
      return (
        <svg {...common}>
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66L9.41 17.41a2 2 0 0 1-2.83-2.83l8.49-8.48" />
        </svg>
      );
    case "sparkle":
      return (
        <svg {...common}>
          <path d="M12 3l1.8 4.6L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.4L12 3z" />
          <path d="M19 15l.7 1.7L21.5 17l-1.8.7L19 19l-.7-1.5L17 17l1.8-.6L19 15z" />
        </svg>
      );
  }
};

// ─────────────────────────── Detail modal ───────────────────────────

interface DetailModalProps {
  inquiry: Json;
  detail: any;
  detailLoading: boolean;
  detailError: string | null;
  onClose: () => void;
}

const DetailModal = ({
  inquiry,
  detail,
  detailLoading,
  detailError,
  onClose,
}: DetailModalProps) => {
  const id = pick(inquiry, "id") ?? "?";
  const question = pick(inquiry, "question") ?? pick(inquiry, "title") ?? "(no question)";
  const status = String(pick(inquiry, "status") ?? "—");
  const ta = String(pick(inquiry, "turnaround-time") ?? "");
  const docs = pick(inquiry, "all-documents");
  const team = pick(inquiry, "submitter-team");

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" onMouseDown={(e) => e.stopPropagation()} style={{ maxWidth: 980 }}>
        <div className="modal-header">
          <h2 style={{ display: "flex", alignItems: "center", gap: 10 }}>
            Inquiry #{String(id)}
            <span className={`status-badge status-${statusColor(status)}`}>
              {prettyStatus(status)}
            </span>
            {ta && <span className={`ext-ta-pill ${turnaroundClass(ta)}`}>{turnaroundLabel(ta)}</span>}
          </h2>
          <button type="button" className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          {/* Question */}
          <div className="answer-card" style={{ marginBottom: 18 }}>
            <div className="answer-label">Question</div>
            <div className="answer-text" style={{ whiteSpace: "pre-wrap" }}>
              {String(question)}
            </div>
          </div>

          {/* Meta grid */}
          <div className="ext-meta-grid">
            <Meta label="Submitter" value={pick(inquiry, "submitter")} />
            <Meta label="Email" value={pick(inquiry, "submitter-email")} />
            <Meta
              label="Team"
              value={typeof team === "object" ? team?.name ?? team?.["name"] : team}
            />
            <Meta label="Assignee" value={pick(inquiry, "assignee")} />
            <Meta label="Reviewer" value={pick(inquiry, "reviewer-names") ?? pick(inquiry, "reviewer")} />
            <Meta label="Category" value={pick(inquiry, "category")} />
            <Meta label="Project type" value={pick(inquiry, "project-types")} />
            <Meta label="Level of evidence" value={pick(inquiry, "level-of-evidence")} />
            <Meta label="Created" value={fmtDateTime(pick(inquiry, "created-at"))} />
            <Meta label="First read" value={fmtDateTime(pick(inquiry, "read-at"))} />
            <Meta label="Approved" value={fmtDateTime(pick(inquiry, "approved-at"))} />
            <Meta label="Published" value={fmtDateTime(pick(inquiry, "published-at"))} />
            <Meta label="Comments" value={pick(inquiry, "comments-count")} />
            <Meta label="Favourites" value={pick(inquiry, "favourite-count")} />
            <Meta label="Star rating" value={pick(inquiry, "star-rating")} />
            <Meta label="UUID" value={pick(inquiry, "uuid")} mono />
          </div>

          {/* Attachments */}
          {Array.isArray(docs) && docs.length > 0 && (
            <div style={{ marginTop: 18 }}>
              <h3 style={{ marginTop: 0 }}>Attachments ({docs.length})</h3>
              <ul className="ext-doc-list">
                {docs.map((d: any, i: number) => {
                  const name = d.file_file_name ?? d.file_name ?? d.name ?? "(unnamed)";
                  const size = d.file_file_size
                    ? `${Math.round(Number(d.file_file_size) / 1024)} KB`
                    : "";
                  const type = d.file_content_type ?? d.content_type ?? "";
                  return (
                    <li key={i}>
                      <span className="ext-doc-name">📎 {name}</span>
                      <span className="cell-muted" style={{ marginLeft: 8 }}>
                        {type} {size && `• ${size}`}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {/* Submitter details (separate API call) */}
          <details style={{ marginTop: 22 }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
              Submitter account details (from /submitter_details)
            </summary>
            <div className="hint" style={{ margin: "8px 0" }}>
              Password hashes and other secrets redacted in this view.
            </div>
            {detailLoading && <div className="empty">Loading…</div>}
            {detailError && <div className="error-banner">{detailError}</div>}
            {!detailLoading && !detailError && detail && (
              <pre className="raw-json">{JSON.stringify(redact(detail), null, 2)}</pre>
            )}
          </details>

          {/* Raw JSON (collapsed) */}
          <details style={{ marginTop: 18 }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
              Full raw JSON (debug)
            </summary>
            <pre className="raw-json" style={{ marginTop: 8 }}>
              {JSON.stringify(inquiry, null, 2)}
            </pre>
          </details>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

const Meta = ({
  label,
  value,
  mono,
}: {
  label: string;
  value: any;
  mono?: boolean;
}) => {
  const str = value == null || value === "" ? "—" : String(value);
  return (
    <div className="ext-meta-item">
      <div className="ext-meta-label">{label}</div>
      <div className={"ext-meta-value" + (mono ? " ext-meta-mono" : "")}>{str}</div>
    </div>
  );
};
