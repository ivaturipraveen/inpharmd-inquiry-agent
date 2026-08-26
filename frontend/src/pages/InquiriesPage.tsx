import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import StatusBadge from "../components/StatusBadge";
import InquiryDetail from "../components/InquiryDetail";
import { api } from "../api";
import type { Inquiry } from "../types";
import { fmtFallbackStatus, fmtFallbackGroup } from "../utils/fallback";

const STATUS_FILTERS = [
  { value: "", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "email_pending", label: "Scheduled" },
  { value: "email_sent", label: "Email Sent" },
  { value: "email_responded", label: "Email Responded" },
  { value: "call_pending", label: "Call Pending" },
  { value: "call_completed", label: "Call Completed" },
  { value: "needs_attention", label: "Needs Attention" },
  { value: "closed", label: "Closed" },
];

// Status buckets — single source of truth, used by both the page-level
// stat tiles AND the per-MUE-group "X responded · Y awaiting · Z draft"
// pills. Without this they drifted: tiles counted only email_responded
// + call_completed, group counted those plus `closed`, so a closed
// inquiry made the two readouts disagree by one.
const RESPONDED_STATUSES = ["email_responded", "call_completed", "closed"];
const AWAITING_STATUSES = ["email_pending", "email_sent", "call_pending"];
const DRAFT_STATUSES = ["draft"];

// Bucket filter values used by the stat-tile click handlers. The
// filter dropdown still uses exact status values from STATUS_FILTERS;
// `matchesStatusFilter` handles both.
const BUCKET_FILTERS = new Set(["responded", "awaiting", "drafts"]);

function matchesStatusFilter(
  status: string,
  filter: string,
  callCompletedAt?: string | null,
): boolean {
  if (!filter) return true;
  if (filter === "responded") return RESPONDED_STATUSES.includes(status);
  if (filter === "awaiting") return AWAITING_STATUSES.includes(status);
  if (filter === "drafts") return DRAFT_STATUSES.includes(status);
  // An inquiry that got a completed call and then a later email reply ends up
  // with status "email_responded" (the email path always overwrites status on
  // the first reply, regardless of a prior completed call — see
  // graph_service.py / imap_service.py / email_inbound.py). Surface it under
  // "Call Completed" too, without reinterpreting call_completed_at for any
  // other status (needs_attention/closed are deliberately excluded).
  if (filter === "call_completed") {
    return (
      status === "call_completed" ||
      (status === "email_responded" && callCompletedAt != null)
    );
  }
  return status === filter;
}

const fmtDate = (s?: string | null) =>
  s ? new Date(s).toLocaleDateString() : "—";

type OutreachTab = "mine" | "all";

export default function InquiriesPage() {
  const [outreachTab, setOutreachTab] = useState<OutreachTab>("mine");
  // Ref so the auto-refresh interval always reads the current tab without
  // needing to be re-created every time outreachTab changes.
  const outreachTabRef = useRef<OutreachTab>("mine");
  const [inquiries, setInquiries] = useState<Inquiry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Inquiry | null>(null);

  const load = useCallback(async (tab?: OutreachTab) => {
    const activeTab = tab ?? outreachTab;
    setLoading(true);
    setError(null);
    try {
      const is = await api.inquiries.list(
        activeTab === "all" ? { all_users: true } : undefined,
      );
      setInquiries(is);
    } catch (err: any) {
      setError(err?.message ?? "Failed to load inquiries.");
    } finally {
      setLoading(false);
    }
  }, [outreachTab]);

  const switchTab = useCallback((tab: OutreachTab) => {
    outreachTabRef.current = tab;
    setOutreachTab(tab);
    setStatusFilter("");
    setSearch("");
    load(tab);
  }, [load]);

  useEffect(() => {
    load(outreachTab);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If the hash carries ?id=NN (e.g. from a Slack deep-link), pop open that inquiry
  // once the list is loaded. One-shot via this ref — without it, closing the modal
  // (which every action now does immediately) would re-trigger this effect and
  // reopen the same inquiry, since the ?id= stays in the URL until a tab change.
  const deepLinkAppliedRef = useRef(false);
  useEffect(() => {
    if (deepLinkAppliedRef.current) return;
    if (loading || inquiries.length === 0 || selected) return;
    const qs = window.location.hash.split("?")[1];
    if (!qs) return;
    const params = new URLSearchParams(qs);
    const idStr = params.get("id");
    if (!idStr) return;
    deepLinkAppliedRef.current = true;
    const target = inquiries.find((i) => i.id === Number(idStr));
    if (target) setSelected(target);
  }, [loading, inquiries, selected]);

  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(null), 6000);
    return () => clearTimeout(t);
  }, [success]);

  // Keep the selected inquiry in sync with the latest list (after actions)
  useEffect(() => {
    if (!selected) return;
    const fresh = inquiries.find((i) => i.id === selected.id);
    if (fresh && fresh !== selected) setSelected(fresh);
  }, [inquiries, selected]);

  // Auto-refresh while anything is in-flight (call dialing, email awaiting reply).
  // Polls every 5s; stops as soon as nothing is transient anymore.
  const hasInFlight = useMemo(
    () =>
      inquiries.some((i) =>
        ["email_pending", "call_pending", "email_sent"].includes(i.status)
      ),
    [inquiries]
  );

  useEffect(() => {
    if (!hasInFlight) return;
    const id = setInterval(() => {
      const params = outreachTabRef.current === "all" ? { all_users: true } : undefined;
      api.inquiries
        .list(params)
        .then(setInquiries)
        .catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, [hasInFlight]);

  useEffect(() => {
    if (!error) return;
    const id = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(id);
  }, [error]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return inquiries.filter((i) => {
      if (!matchesStatusFilter(i.status, statusFilter, i.call_completed_at)) return false;
      if (q) {
        const hay = `${i.subject} ${i.question} ${i.manufacturer?.manufacturer ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [inquiries, statusFilter, search]);

  // Group inquiries forwarded from the same MUE Excel together. The Outreach
  // tab shows ONE collapsible "MUE" row per source_inquiry_uuid (with the N
  // manufacturer children nested inside), and standalone single-manufacturer
  // inquiries as ordinary rows.
  type Row =
    | { kind: "single"; inquiry: Inquiry }
    | { kind: "group"; uuid: string; children: Inquiry[] };

  const rows: Row[] = useMemo(() => {
    const groups = new Map<string, Inquiry[]>();
    const singles: Inquiry[] = [];
    for (const i of filtered) {
      const uuid = (i.source_inquiry_uuid ?? "").trim();
      if (!uuid) {
        singles.push(i);
        continue;
      }
      const existing = groups.get(uuid);
      if (existing) existing.push(i);
      else groups.set(uuid, [i]);
    }

    const out: Row[] = [];
    for (const [uuid, children] of groups) {
      if (children.length === 1) {
        singles.push(children[0]);
      } else {
        // newest first inside the group
        children.sort((a, b) =>
          (b.created_at ?? "").localeCompare(a.created_at ?? ""),
        );
        out.push({ kind: "group", uuid, children });
      }
    }
    for (const s of singles) out.push({ kind: "single", inquiry: s });

    // Sort everything together by newest created_at — groups use their
    // newest child's date, singles use their own. Matches InpharmD Inquiries order.
    out.sort((a, b) => {
      const ax = a.kind === "group" ? (a.children[0]?.created_at ?? "") : a.inquiry.created_at ?? "";
      const bx = b.kind === "group" ? (b.children[0]?.created_at ?? "") : b.inquiry.created_at ?? "";
      return bx.localeCompare(ax);
    });
    return out;
  }, [filtered]);

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = (uuid: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(uuid)) next.delete(uuid);
      else next.add(uuid);
      return next;
    });
  };

  const stats = useMemo(() => {
    let drafts = 0;
    let awaiting = 0;
    let responded = 0;
    for (const i of inquiries) {
      if (DRAFT_STATUSES.includes(i.status)) drafts++;
      else if (AWAITING_STATUSES.includes(i.status)) awaiting++;
      else if (RESPONDED_STATUSES.includes(i.status)) responded++;
    }
    return { total: inquiries.length, drafts, awaiting, responded };
  }, [inquiries]);

  const handleAction = async (action: string, payload?: any) => {
    if (!selected) return;
    // Close the modal immediately — success/error banners render on the
    // page behind it, so leaving the modal open hides them until the user
    // closes it manually. The action still runs to completion below.
    const current = selected;
    setSelected(null);
    try {
      switch (action) {
        case "sendEmail":
          await api.inquiries.sendEmail(current.id);
          setSuccess("Email scheduled — will send in ~30 min.");
          break;
        case "cancelScheduledEmail":
          await api.inquiries.cancelScheduledEmail(current.id);
          setSuccess("Scheduled email cancelled. Inquiry returned to draft.");
          break;
        case "editDraft":
          await api.inquiries.update(current.id, { subject: payload.subject, question: payload.question });
          setSuccess("Draft updated.");
          break;
        case "editScheduledEmail":
          await api.inquiries.editScheduledEmailContent(current.id, payload.subject, payload.question);
          setSuccess("Email content updated.");
          break;
        case "sendNow":
          await api.inquiries.sendEmailNow(current.id);
          setSuccess("Email sent immediately.");
          break;
        case "recordEmailResponse":
          await api.inquiries.recordEmailResponse(current.id, payload);
          setSuccess("Email response saved.");
          break;
        case "triggerCall":
          await api.inquiries.triggerCall(current.id);
          setSuccess("Call queued.");
          break;
        case "recordCallResult":
          await api.inquiries.recordCallResult(
            current.id,
            payload.summary,
            payload.transcript
          );
          setSuccess("Call result saved.");
          break;
        case "close":
          await api.inquiries.close(current.id);
          setSuccess("Inquiry closed.");
          break;
        case "extractAnswer":
          await api.inquiries.extractAnswer(current.id);
          setSuccess("Answer extracted from transcript.");
          break;
        case "resetRetries":
          await api.inquiries.resetRetries(current.id);
          setSuccess("Retries reset. Inquiry returned to draft.");
          break;
        default:
          return;
      }
      load();
    } catch (err: any) {
      setError(err?.message ?? "Action failed.");
    }
  };

  const handleDelete = async () => {
    if (!selected) return;
    const current = selected;
    setSelected(null);
    try {
      await api.inquiries.remove(current.id);
      setSuccess("Inquiry deleted.");
      load();
    } catch (err: any) {
      setError(err?.message ?? "Delete failed.");
    }
  };

  return (
    <>
      <section className="page-head">
        <h1>Manufacturer Outreach</h1>
        <p>
          Send a question to a manufacturer by email. If they don't respond
          within your fallback window, our voice agent calls them and brings
          the answer back here.
        </p>
      </section>

      <div className="outreach-tabs">
        <button
          type="button"
          className={`outreach-tab ${outreachTab === "mine" ? "outreach-tab-active" : ""}`}
          onClick={() => outreachTab !== "mine" && switchTab("mine")}
        >
          My Outreaches
        </button>
        <button
          type="button"
          className={`outreach-tab ${outreachTab === "all" ? "outreach-tab-active" : ""}`}
          onClick={() => outreachTab !== "all" && switchTab("all")}
        >
          All Outreaches
        </button>
      </div>

      <div className="stats-grid stats-grid-4 stats-grid-compact">
        <button
          type="button"
          className={`stat-card stat-card-btn ${statusFilter === "" ? "stat-card-active" : ""}`}
          onClick={() => setStatusFilter("")}
        >
          <div className="stat-label">Total Inquiries</div>
          <div className="stat-value">{stats.total}</div>
        </button>
        <button
          type="button"
          className={`stat-card stat-card-btn ${statusFilter === "drafts" ? "stat-card-active" : ""}`}
          onClick={() => setStatusFilter(statusFilter === "drafts" ? "" : "drafts")}
        >
          <div className="stat-label">Drafts</div>
          <div className="stat-value">{stats.drafts}</div>
        </button>
        <button
          type="button"
          className={`stat-card stat-card-btn ${statusFilter === "awaiting" ? "stat-card-active" : ""}`}
          onClick={() => setStatusFilter(statusFilter === "awaiting" ? "" : "awaiting")}
        >
          <div className="stat-label">Awaiting</div>
          <div className="stat-value">{stats.awaiting}</div>
        </button>
        <button
          type="button"
          className={`stat-card stat-card-btn ${statusFilter === "responded" ? "stat-card-active" : ""}`}
          onClick={() => setStatusFilter(statusFilter === "responded" ? "" : "responded")}
        >
          <div className="stat-label">Responded</div>
          <div className="stat-value">{stats.responded}</div>
        </button>
      </div>

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
              placeholder="Search subject, question, manufacturer…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className="filter-select"
            // Show the dropdown as "All statuses" when a bucket filter
            // (set via the stat tiles) is active — bucket values aren't
            // options, so a raw value= would render blank and look broken.
            value={BUCKET_FILTERS.has(statusFilter) ? "" : statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div className="filter-meta">
          Showing <strong>{filtered.length}</strong> of {inquiries.length} inquiries
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {success && <div className="success-banner">{success}</div>}

      <div className="table-card">
        {loading ? (
          <div className="empty">
            <div className="empty-title">Loading…</div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty">
            <div className="empty-title">No inquiries yet</div>
            <div className="empty-sub">
              No outreaches match your current filters.
            </div>
          </div>
        ) : (
          <div className="table-scroll">
            <table className="data-table outreach-table">
              <colgroup>
                <col style={{ width: 90 }} />
                <col />
                <col style={{ width: "22%" }} />
                <col style={{ width: 160 }} />
                <col style={{ width: 110 }} />
                <col style={{ width: 110 }} />
              </colgroup>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Subject</th>
                  <th>Manufacturer</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Fallback</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  if (row.kind === "single") {
                    const i = row.inquiry;
                    return (
                      <tr
                        key={`s-${i.id}`}
                        className="row-clickable"
                        onClick={async () => {
                          setSelected(i);
                          try {
                            const fresh = await api.inquiries.get(i.id);
                            setSelected((cur) => (cur && cur.id === fresh.id ? fresh : cur));
                          } catch {
                            /* keep cached */
                          }
                        }}
                      >
                        <td className="cell-muted">#{i.id}</td>
                        <td className="cell-wrap">
                          {/* subject is the [InpharmD #id]-tagged outbound email
                              subject, not meant for display — show the actual
                              question instead, matching the MUE group row below. */}
                          <div className="cell-primary">{i.question}</div>
                        </td>
                        <td>
                          {i.is_test_call ? (
                            <div>
                              <div>{i.manufacturer?.manufacturer ?? "No manufacturer matched"}</div>
                              <span style={{ fontSize: "0.7rem", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", opacity: 0.5 }}>Test</span>
                            </div>
                          ) : (
                            i.manufacturer?.manufacturer ?? "—"
                          )}
                        </td>
                        <td className="status-td">
                          <div style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                            <StatusBadge status={i.status} />
                            {i.status === "email_pending" && i.email_scheduled_for && (
                              <span className="cell-muted" style={{ fontSize: "0.75rem", paddingLeft: "10px" }}>
                                {new Date(i.email_scheduled_for) > new Date()
                                  ? `Sends at ${new Date(i.email_scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                                  : "Sending soon…"}
                              </span>
                            )}
                            {i.status === "email_sent" && i.call_scheduled_for && (
                              <span className="cell-muted" style={{ fontSize: "0.75rem", paddingLeft: "10px" }}>
                                {new Date(i.call_scheduled_for) > new Date()
                                  ? `Fallback call at ${new Date(i.call_scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                                  : "Fallback call pending…"}
                              </span>
                            )}
                            {i.email_sent_at && (i.status === "call_pending" || i.status === "call_completed" || i.status === "needs_attention") && (
                              <span className="cell-muted" style={{ fontSize: "0.75rem", paddingLeft: "10px" }}>
                                via fallback call
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="cell-muted">{fmtDate(i.created_at)}</td>
                        <td className="cell-muted">{fmtFallbackStatus(i.manufacturer?.fallback_call_enabled, i.fallback_after_hours, i.manufacturer?.mi_phone)}</td>
                      </tr>
                    );
                  }

                  const open = expandedGroups.has(row.uuid);
                  const sample = row.children[0];
                  const total = row.children.length;
                  const responded = row.children.filter((c) =>
                    RESPONDED_STATUSES.includes(c.status),
                  ).length;
                  const sent = row.children.filter((c) =>
                    AWAITING_STATUSES.includes(c.status),
                  ).length;
                  const drafts = row.children.filter((c) =>
                    DRAFT_STATUSES.includes(c.status),
                  ).length;
                  // children are already sorted newest-first; use [0] to match sort order
                  const groupCreated = row.children[0]?.created_at ?? "";
                  const groupFallback = fmtFallbackGroup(
                    row.children.map((c) => ({
                      enabled: c.manufacturer?.fallback_call_enabled,
                      hours: c.fallback_after_hours,
                      miPhone: c.manufacturer?.mi_phone,
                    }))
                  );
                  return (
                    <Fragment key={`g-${row.uuid}`}>
                      <tr
                        className={`mue-group-row ${open ? "mue-group-open" : ""}`}
                        onClick={() => toggleGroup(row.uuid)}
                      >
                        <td className="mue-group-id">
                          <span className="mue-caret" aria-hidden>
                            {open ? "▾" : "▸"}
                          </span>
                          <span className="mue-badge">MUE</span>
                        </td>
                        <td className="mue-subject-cell cell-wrap">
                          {/* Subject is now per-inquiry (unique [InpharmD #id] per
                              child), so it can't represent the whole MUE group —
                              show the shared original MUE title/question instead. */}
                          <span className="cell-primary">{sample.question}</span>
                        </td>
                        <td className="mue-stats-cell">
                          {open ? (
                            <div className="mue-mfr-name-list">
                              {row.children.map((c) => (
                                <div key={c.id}>
                                  {c.is_test_call
                                    ? (c.manufacturer?.manufacturer ?? c.test_call_phone ?? "No manufacturer matched")
                                    : (c.manufacturer?.manufacturer ?? "—")}
                                </div>
                              ))}
                            </div>
                          ) : (
                            <span className="mue-mfr-count">
                              {total} manufacturers
                            </span>
                          )}
                        </td>
                        <td className="status-td">
                          {row.children.every((c) => c.status === "closed") ? (
                            <StatusBadge status="closed" />
                          ) : (
                            <>
                              <div className="mue-progress">
                                <div
                                  className="mue-progress-bar"
                                  style={{
                                    width: `${total ? (responded / total) * 100 : 0}%`,
                                  }}
                                />
                              </div>
                              <div className="mue-progress-text">
                                {responded} / {total} responses
                              </div>
                            </>
                          )}
                        </td>
                        <td className="cell-muted">{fmtDate(groupCreated)}</td>
                        <td className="cell-muted">{groupFallback}</td>
                      </tr>
                      {open &&
                        row.children.map((c) => (
                          <tr
                            key={`c-${c.id}`}
                            className="row-clickable mue-child-row"
                            onClick={async () => {
                              setSelected(c);
                              try {
                                const fresh = await api.inquiries.get(c.id);
                                setSelected((cur) =>
                                  cur && cur.id === fresh.id ? fresh : cur,
                                );
                              } catch {
                                /* keep cached */
                              }
                            }}
                          >
                            <td className="cell-muted mue-child-id">
                              <span className="mue-child-rail" />
                              #{c.id}
                            </td>
                            <td>
                              <div className="mue-child-sub">
                                {c.email_response
                                  ? c.email_response.slice(0, 120) +
                                    (c.email_response.length > 120 ? "…" : "")
                                  : c.status === "draft"
                                  ? "Not sent yet"
                                  : c.status === "email_pending"
                                  ? `Scheduled — sends at ${c.email_scheduled_for ? new Date(c.email_scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "..."}`
                                  : "Waiting for reply"}
                              </div>
                            </td>
                            <td>{c.manufacturer?.manufacturer ?? "—"}</td>
                            <td className="status-td">
                              <div style={{ display: "inline-flex", flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                                <StatusBadge status={c.status} />
                                {c.status === "email_sent" && c.call_scheduled_for && (
                                  <span className="cell-muted" style={{ fontSize: "0.75rem", paddingLeft: "10px" }}>
                                    {new Date(c.call_scheduled_for) > new Date()
                                      ? `Fallback call at ${new Date(c.call_scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
                                      : "Fallback call pending…"}
                                  </span>
                                )}
                                {c.email_sent_at && (c.status === "call_pending" || c.status === "call_completed" || c.status === "needs_attention") && (
                                  <span className="cell-muted" style={{ fontSize: "0.75rem", paddingLeft: "10px" }}>
                                    via fallback call
                                  </span>
                                )}
                              </div>
                            </td>
                            <td className="cell-muted">{fmtDate(c.created_at)}</td>
                            <td className="cell-muted">{fmtFallbackStatus(c.manufacturer?.fallback_call_enabled, c.fallback_after_hours, c.manufacturer?.mi_phone)}</td>
                          </tr>
                        ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selected && (
        <InquiryDetail
          inquiry={selected}
          onClose={() => setSelected(null)}
          onAction={handleAction}
          onDelete={handleDelete}
        />
      )}
    </>
  );
}
