import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiMeta } from "../api";
import { startContactManufacturerFlow } from "./ContactManufacturerPage";

// Open MUE inquiries endpoint shape:
//   { data: [ {
//       inquiry_uuid: string,
//       title: string,
//       inquiry_submitter: string,
//       inquiry_types: string[],
//       attachments: [{ id, file_name, doc_url }],
//       inquiry_submitter_details: { id, email, first_name, last_name, ... }
//     } ] }

type Attachment = { id: number; file_name: string; doc_url: string };

interface SubmitterDetails {
  id?: number;
  email?: string;
  first_name?: string;
  last_name?: string;
  [k: string]: unknown;
}

interface MueInquiry {
  inquiry_uuid: string;
  title: string;
  inquiry_submitter?: string;
  inquiry_types?: string[];
  attachments?: Attachment[];
  inquiry_submitter_details?: SubmitterDetails;
}

const truncate = (s: string, n = 80): string =>
  s.length <= n ? s : s.slice(0, n) + "…";

const shortUuid = (uuid: string): string =>
  uuid ? uuid.slice(0, 8) : "—";

const submitterEmail = (i: MueInquiry): string =>
  i.inquiry_submitter_details?.email ?? "";

const submitterDisplay = (i: MueInquiry): string => {
  const det = i.inquiry_submitter_details;
  if (det?.first_name || det?.last_name) {
    return `${det.first_name ?? ""} ${det.last_name ?? ""}`.trim();
  }
  return i.inquiry_submitter ?? det?.email ?? "—";
};

export default function ExternalInquiriesPage() {
  const [raw, setRaw] = useState<any>(null);
  const [meta, setMeta] = useState<ApiMeta | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [withAttachmentsOnly, setWithAttachmentsOnly] = useState(false);
  const [selected, setSelected] = useState<MueInquiry | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const menuWrapRef = useRef<HTMLDivElement | null>(null);

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

  // Close the row menu when clicking outside.
  useEffect(() => {
    if (!openMenuId) return;
    const onDocClick = (e: MouseEvent) => {
      if (!menuWrapRef.current) return;
      if (!menuWrapRef.current.contains(e.target as Node)) setOpenMenuId(null);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [openMenuId]);

  // Tick once a while so "loaded N min ago" stays current.
  const [, forceTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => forceTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const inquiries: MueInquiry[] = useMemo(() => {
    if (!raw) return [];
    const list: any[] = Array.isArray(raw)
      ? raw
      : Array.isArray(raw.data)
      ? raw.data
      : Array.isArray(raw.inquiries)
      ? raw.inquiries
      : Array.isArray(raw.results)
      ? raw.results
      : [];
    return list
      .map((row: any): MueInquiry | null => {
        // Tolerate two shapes:
        //   1) flat MUE shape: { inquiry_uuid, title, … }
        //   2) JSON:API legacy: { id, type, attributes: { … } }
        if (row?.inquiry_uuid || row?.title) {
          return row as MueInquiry;
        }
        const a = row?.attributes ?? {};
        if (!a.title && !a.question && !row.id) return null;
        return {
          inquiry_uuid: String(row.id ?? a.uuid ?? a.inquiry_uuid ?? ""),
          title: String(a.title ?? a.question ?? ""),
          inquiry_submitter: a.submitter ?? a["submitter-email"] ?? undefined,
          inquiry_types: a["inquiry-types"] ?? a.inquiry_types ?? [],
          attachments: a.attachments ?? a["all-documents"] ?? [],
          inquiry_submitter_details: a["submitter-details"] ?? undefined,
        };
      })
      .filter(Boolean) as MueInquiry[];
  }, [raw]);

  // ----- Stats -----
  const stats = useMemo(() => {
    const byType: Record<string, number> = {};
    const submitters = new Set<string>();
    let withDocs = 0;
    let totalAttachments = 0;
    for (const i of inquiries) {
      const types = i.inquiry_types ?? [];
      for (const t of types) byType[t] = (byType[t] || 0) + 1;
      const email = submitterEmail(i) || i.inquiry_submitter || "";
      if (email) submitters.add(email);
      const atts = i.attachments ?? [];
      if (atts.length > 0) withDocs++;
      totalAttachments += atts.length;
    }
    return {
      total: inquiries.length,
      withDocs,
      submitters: submitters.size,
      totalAttachments,
      byType,
    };
  }, [inquiries]);

  const allTypes = useMemo(
    () => Object.entries(stats.byType).sort((a, b) => b[1] - a[1]),
    [stats.byType],
  );

  // ----- Filter -----
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return inquiries.filter((i) => {
      if (typeFilter && !(i.inquiry_types ?? []).includes(typeFilter)) return false;
      if (withAttachmentsOnly && (i.attachments?.length ?? 0) === 0) return false;
      if (q) {
        const hay = [
          i.title,
          i.inquiry_submitter ?? "",
          submitterEmail(i),
          ...(i.inquiry_types ?? []),
          ...(i.attachments ?? []).map((a) => a.file_name),
          i.inquiry_uuid,
        ]
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [inquiries, search, typeFilter, withAttachmentsOnly]);

  const startContact = (i: MueInquiry) => {
    setOpenMenuId(null);
    startContactManufacturerFlow({
      uuid: i.inquiry_uuid,
      title: i.title,
      submitter: submitterDisplay(i),
      type: (i.inquiry_types ?? [])[0],
      attachments: i.attachments,
    });
  };

  return (
    <>
      <section className="page-head">
        <h1>InpharmD Inquiries</h1>
        <p>
          Open Medication Use Evaluation (MUE) inquiries from the InpharmD
          platform — {stats.total.toLocaleString()} total,{" "}
          <strong>{stats.withDocs}</strong> with attachments,{" "}
          <strong>{stats.submitters}</strong> unique submitters.
        </p>
        <CacheBadge meta={meta} loadedAt={lastLoadedAt} />
      </section>

      {/* Headline stats */}
      <div className="ext-stats">
        <StatTile label="Total inquiries" value={stats.total} tone="neutral" icon="list" />
        <StatTile label="With attachments" value={stats.withDocs} tone="info" icon="paperclip" />
        <StatTile label="Attachments" value={stats.totalAttachments} tone="neutral" icon="paperclip" />
        <StatTile label="Submitters" value={stats.submitters} tone="info" icon="dot" />
        <StatTile label="Inquiry types" value={allTypes.length} tone="neutral" icon="list" />
      </div>

      {/* By-type chip filters */}
      {allTypes.length > 0 && (
        <div className="ext-section">
          <div className="ext-section-head">
            <h3>By inquiry type</h3>
            {typeFilter && (
              <button type="button" className="ext-clear" onClick={() => setTypeFilter("")}>
                Clear filter
              </button>
            )}
          </div>
          <div className="ext-chips">
            <button
              type="button"
              className={`ext-chip ${!typeFilter ? "ext-chip-active" : ""}`}
              onClick={() => setTypeFilter("")}
            >
              All <span className="ext-chip-num">{stats.total}</span>
            </button>
            {allTypes.map(([t, n]) => (
              <button
                key={t}
                type="button"
                className={`ext-chip ext-chip-info ${
                  typeFilter === t ? "ext-chip-active" : ""
                }`}
                onClick={() => setTypeFilter(typeFilter === t ? "" : t)}
              >
                {t} <span className="ext-chip-num">{n}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Search + toggles */}
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
              placeholder="Search title, submitter, file name, uuid…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <label className="ext-toggle">
            <input
              type="checkbox"
              checked={withAttachmentsOnly}
              onChange={(e) => setWithAttachmentsOnly(e.target.checked)}
            />
            With attachments
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
          {(typeFilter || withAttachmentsOnly || search) && " (filtered)"}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="table-card">
        {loading ? (
          <div className="empty">
            <div className="empty-title">Loading from InpharmD…</div>
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
                <col className="col-type" />
                <col className="col-docs" />
                <col className="col-actions" />
              </colgroup>
              <thead>
                <tr>
                  <th>UUID</th>
                  <th>Title</th>
                  <th>Submitter</th>
                  <th>Type</th>
                  <th className="th-center" title="Attachments">📎</th>
                  <th className="th-center" aria-label="Actions"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((i) => {
                  const docCount = i.attachments?.length ?? 0;
                  const types = i.inquiry_types ?? [];
                  return (
                    <tr
                      key={i.inquiry_uuid}
                      onClick={() => setSelected(i)}
                    >
                      <td className="ext-id-cell">
                        <span className="ext-id mono-small">
                          {shortUuid(i.inquiry_uuid)}
                        </span>
                      </td>
                      <td className="ext-question-cell">
                        <div className="ext-question-text" title={i.title}>
                          {truncate(i.title, 120)}
                        </div>
                      </td>
                      <td className="ext-submitter-cell">
                        <div className="ext-submitter-name">
                          {submitterDisplay(i)}
                        </div>
                        {submitterEmail(i) && (
                          <div className="ext-submitter-email">
                            {submitterEmail(i)}
                          </div>
                        )}
                      </td>
                      <td className="ext-pill-cell">
                        {types.length > 0 ? (
                          <span className="ext-chip ext-chip-info ext-chip-static">
                            {truncate(types[0], 32)}
                            {types.length > 1 && ` +${types.length - 1}`}
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
                      <td
                        className="cell-actions"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div
                          className="row-menu"
                          ref={openMenuId === i.inquiry_uuid ? menuWrapRef : undefined}
                        >
                          <button
                            type="button"
                            className={`menu-trigger ${
                              openMenuId === i.inquiry_uuid ? "menu-trigger-open" : ""
                            }`}
                            aria-label="Row actions"
                            title="Actions"
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuId(
                                openMenuId === i.inquiry_uuid ? null : i.inquiry_uuid,
                              );
                            }}
                          >
                            <svg viewBox="0 0 20 20" fill="currentColor">
                              <circle cx="10" cy="4" r="1.6" />
                              <circle cx="10" cy="10" r="1.6" />
                              <circle cx="10" cy="16" r="1.6" />
                            </svg>
                          </button>
                          {openMenuId === i.inquiry_uuid && (
                            <div className="menu-popover" role="menu">
                              <button
                                type="button"
                                className="menu-item"
                                onClick={() => startContact(i)}
                              >
                                <svg
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <path d="M3 11l18-8-8 18-2-8-8-2z" />
                                </svg>
                                Contact manufacturer
                              </button>
                            </div>
                          )}
                        </div>
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
          onClose={() => setSelected(null)}
          onContact={() => {
            const i = selected;
            setSelected(null);
            startContact(i);
          }}
        />
      )}
    </>
  );
}

// ───────────────────────── Detail modal ─────────────────────────

const DetailModal = ({
  inquiry,
  onClose,
  onContact,
}: {
  inquiry: MueInquiry;
  onClose: () => void;
  onContact: () => void;
}) => {
  const det = inquiry.inquiry_submitter_details ?? {};
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Inquiry details</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          <div className="contact-context-card">
            <div className="contact-context-row">
              <span className="contact-context-label">Title</span>
              <span className="contact-context-value">{inquiry.title || "—"}</span>
            </div>
            <div className="contact-context-row">
              <span className="contact-context-label">UUID</span>
              <span className="contact-context-value mono-small">
                {inquiry.inquiry_uuid}
              </span>
            </div>
            <div className="contact-context-row">
              <span className="contact-context-label">Submitter</span>
              <span className="contact-context-value">
                {submitterDisplay(inquiry)}
                {det.email && (
                  <div className="ext-submitter-email">{det.email}</div>
                )}
              </span>
            </div>
            {(inquiry.inquiry_types ?? []).length > 0 && (
              <div className="contact-context-row">
                <span className="contact-context-label">Types</span>
                <span className="contact-context-value">
                  {(inquiry.inquiry_types ?? []).map((t) => (
                    <span key={t} className="ext-chip ext-chip-info ext-chip-static">
                      {t}
                    </span>
                  ))}
                </span>
              </div>
            )}
            {(inquiry.attachments ?? []).length > 0 && (
              <div className="contact-context-row">
                <span className="contact-context-label">
                  Attachments ({inquiry.attachments?.length})
                </span>
                <span className="contact-context-value">
                  <ul className="contact-attachment-list">
                    {(inquiry.attachments ?? []).map((a) => (
                      <li key={a.id}>
                        <a href={a.doc_url} target="_blank" rel="noreferrer">
                          📎 {a.file_name}
                        </a>
                      </li>
                    ))}
                  </ul>
                </span>
              </div>
            )}
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
          <button type="button" className="btn btn-primary" onClick={onContact}>
            Contact manufacturer →
          </button>
        </div>
      </div>
    </div>
  );
};

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
        {meta.cacheAgeSeconds != null && <> · cached {fmtAge(meta.cacheAgeSeconds)}</>}
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

type IconKey = "list" | "paperclip" | "dot";

interface StatTileProps {
  label: string;
  value: number;
  tone: "good" | "warn" | "bad" | "info" | "neutral";
  icon: IconKey;
}

const StatTile = ({ label, value, tone, icon }: StatTileProps) => (
  <div className={`ext-stat ext-stat-${tone}`}>
    <div className="ext-stat-head">
      <div className="ext-stat-label">{label}</div>
      <div className="ext-stat-icon" aria-hidden>
        <StatIcon name={icon} />
      </div>
    </div>
    <div className="ext-stat-value">{value.toLocaleString()}</div>
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
    case "paperclip":
      return (
        <svg {...common}>
          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66L9.41 17.41a2 2 0 0 1-2.83-2.83l8.49-8.48" />
        </svg>
      );
    case "dot":
      return (
        <svg {...common}>
          <circle cx="18" cy="6" r="3" fill="currentColor" />
          <path d="M21 12.5V18a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3h5.5" />
        </svg>
      );
  }
};
