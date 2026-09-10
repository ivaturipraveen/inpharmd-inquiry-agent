import { FC, ReactNode, useEffect, useState } from "react";
import StatusBadge from "./StatusBadge";
import type { Inquiry } from "../types";
import { renderBold } from "../utils/renderBold";
import { fmtFallbackStatus } from "../utils/fallback";
import { resolvePreferredChannel, isEmailReachable, isCallReachable, isWebFormReachable } from "../utils/channelResolution";

interface Props {
  inquiry: Inquiry;
  onClose: () => void;
  onAction: (action: string, payload?: any) => Promise<void>;
  onDelete: () => Promise<void>;
}

const fmtDate = (s?: string | null) =>
  s ? new Date(s).toLocaleString() : null;


const InquiryDetail: FC<Props> = ({ inquiry, onClose, onAction, onDelete }) => {
  const [busy, setBusy] = useState(false);
  const [emailReply, setEmailReply] = useState("");
  const [callSummary, setCallSummary] = useState("");
  const [callTranscript, setCallTranscript] = useState("");
  const [editingScheduled, setEditingScheduled] = useState(false);
  const [editingDraft, setEditingDraft] = useState(false);
  const [editSubject, setEditSubject] = useState("");
  const [editQuestion, setEditQuestion] = useState("");
  const [followupBody, setFollowupBody] = useState("");

  // When opened via a Slack "View transcript" deep-link (#inquiries?id=N&focus=transcript)
  // scroll the transcript into view after the modal renders.
  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.split("?")[1] || "");
    if (params.get("focus") !== "transcript") return;
    const t = setTimeout(() => {
      document
        .getElementById("call-transcript")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 60);
    return () => clearTimeout(t);
  }, [inquiry.id]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  const m = inquiry.manufacturer;
  const isTestCall = inquiry.is_test_call ?? false;

  // preferred_channel decides which action a draft offers — never inferred
  // from populated fields. Only relevant to draft gating; other statuses reflect an already-dispatched channel.
  const preferredChannel = resolvePreferredChannel(m);
  const emailReachable = isEmailReachable(m);
  const callReachable = isCallReachable(m);
  const webFormReachable = isWebFormReachable(m);

  // Mirrors backend _call_in_flight exactly (not just status=="call_pending") —
  // a closed inquiry's follow-up call never changes status, so this can't rely on it alone.
  const callInFlight = !!(
    inquiry.call_conversation_id &&
    inquiry.call_scheduled_for &&
    (!inquiry.call_completed_at ||
      new Date(inquiry.call_completed_at).getTime() < new Date(inquiry.call_scheduled_for).getTime())
  );
  const isDraft = inquiry.status === "draft";
  const isCallScheduled = inquiry.status === "call_scheduled";
  const isScheduled = inquiry.status === "email_pending";
  const canRecordEmail = inquiry.status === "email_sent";
  // The user can always call again, regardless of status — blocked only by no
  // phone, an in-flight call, or is_test_call (backend enforces the same three).
  const canTriggerCall = !isTestCall && !callInFlight && callReachable;
  // Statuses where the inquiry is already resolved — calling from here is a
  // follow-up, not the original dispatch/retry flow, so labeled accordingly.
  const isResolvedStatus = ["closed", "email_responded", "call_completed"].includes(inquiry.status);
  // callInFlight is included so the recovery form still appears for a closed
  // inquiry's stuck follow-up call — status alone stays "closed" there.
  const canRecordCall = inquiry.status === "call_pending" || inquiry.status === "needs_attention" || callInFlight;
  const canClose = !["closed"].includes(inquiry.status);

  const retryCount = inquiry.retry_count ?? 0;
  const maxRetries = inquiry.max_retries ?? 2;
  const nextRetry = inquiry.next_retry_at ? new Date(inquiry.next_retry_at) : null;
  const retryButtonLabel = callInFlight
    ? "Call in progress…"
    : isResolvedStatus
    ? "Call Now"
    : inquiry.status === "needs_attention"
    ? "Retry Call Manually"
    : retryCount > 0
    ? `Trigger Call (retried ${retryCount}/${maxRetries})`
    : "Trigger Call Now";

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal modal-wide" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="detail-head-meta">
              <StatusBadge status={inquiry.status} />
              {isTestCall && (
                <span style={{ fontSize: "0.7rem", fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase", padding: "2px 6px", borderRadius: 4, background: "var(--color-warn-bg, #fef3c7)", color: "var(--color-warn-text, #92400e)", marginLeft: 4 }}>
                  Test Call
                </span>
              )}
              <span className="meta-dot">·</span>
              <span className="meta-text">
                Inquiry #{inquiry.id} · created {fmtDate(inquiry.created_at)}
              </span>
            </div>
            <h2>{inquiry.subject}</h2>
            {isTestCall && (
              <div className="detail-head-sub">
                <strong>{m?.manufacturer ?? "No manufacturer matched"}</strong>
                {inquiry.test_call_phone && <> · {inquiry.test_call_phone}</>}
              </div>
            )}
            {!isTestCall && m && (
              <div className="detail-head-sub">
                To: <strong>{m.manufacturer}</strong>
                {m.official_mi_email && (
                  <>
                    {" · "}
                    <a href={`mailto:${m.official_mi_email}`}>
                      {m.official_mi_email}
                    </a>
                  </>
                )}
                {m.mi_phone && <> · {m.mi_phone}</>}
              </div>
            )}
          </div>
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
          <div className="detail-section">
            <div className="detail-label">Question</div>
            <div className="detail-prose">{inquiry.question}</div>
            {inquiry.mue_details && (
              <div className="detail-prose">{inquiry.mue_details}</div>
            )}
          </div>

          {/* Shows once final_answer/pdf_summary/pdf_url exist — order is: direct
              response, AI attachment summary, download link. */}
          {(inquiry.final_answer || inquiry.pdf_summary || inquiry.pdf_url) && (
            <div className="detail-section answer-box answer-box-prominent">
              <div className="answer-label">
                <span className="answer-icon">✓</span>
                Final Answer
              </div>
              {inquiry.final_answer && (
                <div className="detail-prose">{renderBold(inquiry.final_answer)}</div>
              )}

              {/* Render attachments grouped by reply when email_replies is available;
                  fall back to flat list from inbound_attachments for old records. */}
              {(() => {
                const attSummaryLabel = (filename?: string | null) => {
                  const ext = (filename || "").split(".").pop()?.toLowerCase();
                  return ext === "csv" ? "CSV Summary"
                    : ext === "xlsx" || ext === "xls" ? "Spreadsheet Summary"
                    : ext === "docx" || ext === "doc" ? "Document Summary"
                    : "PDF Summary";
                };
                const renderAtt = (att: { id: number; url: string; filename?: string | null; summary?: string | null }, i: number, showFilename: boolean) => (
                  <div key={att.id > 0 ? att.id : `att-${i}`}>
                    {att.summary && (
                      <div className="answer-subsection">
                        <div className="answer-sublabel">
                          {attSummaryLabel(att.filename)}
                          {showFilename && att.filename && (
                            <span style={{ fontWeight: 400, color: "var(--muted)", marginLeft: 4 }}>
                              — {att.filename}
                            </span>
                          )}
                        </div>
                        <div className="detail-prose">{renderBold(att.summary)}</div>
                      </div>
                    )}
                    {att.url && (
                      <div className="answer-pdf-link">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <path d="M14 2v6h6" />
                        </svg>
                        <a href={att.url} target="_blank" rel="noreferrer">
                          {att.filename || "Open attachment"}
                        </a>
                      </div>
                    )}
                  </div>
                );

                const replies = (inquiry.email_replies ?? []).filter(r => r.attachments?.length);
                if (replies.length > 0) {
                  return replies.map((reply, replyIdx) => (
                    <div key={reply.id}>
                      {replies.length > 1 && (
                        <div className="answer-reply-divider">
                          Reply {replyIdx + 1} · {fmtDate(reply.sent_at)}
                        </div>
                      )}
                      {reply.attachments.map((att, i) =>
                        renderAtt(att, i, reply.attachments.length > 1)
                      )}
                    </div>
                  ));
                }

                // Legacy: flat list from inbound_attachments or scalar pdf fields.
                const atts = inquiry.inbound_attachments?.length
                  ? inquiry.inbound_attachments
                  : inquiry.pdf_url
                  ? [{ id: 0, url: inquiry.pdf_url, filename: inquiry.pdf_filename, summary: inquiry.pdf_summary }]
                  : [];
                return atts.map((att, i) => renderAtt(att, i, atts.length > 1));
              })()}
            </div>
          )}

          {/* Email Thread — inbound replies + outbound follow-ups, chronological,
              only rendered when email_replies data is available. */}
          {(inquiry.email_replies?.length ?? 0) > 0 && (() => {
            let inboundCount = 0;
            return (
            <div className="detail-section">
              <div className="detail-label">Email Thread</div>
              {inquiry.email_replies!.map((reply, idx) => {
                const isOutbound = reply.direction === "outbound";
                if (!isOutbound) inboundCount += 1;
                return (
                <div
                  key={reply.id}
                  style={{
                    borderTop: idx > 0 ? "1px solid var(--line)" : undefined,
                    paddingTop: idx > 0 ? "12px" : "8px",
                    marginTop: idx > 0 ? "12px" : "4px",
                  }}
                >
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline", marginBottom: 6, flexWrap: "wrap" }}>
                    <span style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: isOutbound ? "var(--muted)" : "var(--brand-orange)" }}>
                      {isOutbound ? "You (follow-up)" : `Reply ${inboundCount}`}
                    </span>
                    {reply.sender_email && (
                      <span style={{ fontSize: "0.8rem", color: "var(--muted)" }}>{reply.sender_email}</span>
                    )}
                    <span style={{ fontSize: "0.8rem", color: "var(--muted)", marginLeft: "auto" }}>
                      {fmtDate(reply.sent_at)}
                    </span>
                  </div>
                  {reply.body && (
                    <div className="detail-prose">{renderBold(reply.body)}</div>
                  )}
                  {(reply.attachments?.length ?? 0) > 0 && (
                    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                      {reply.attachments.map((att) => {
                        const isImage =
                          att.content_type?.startsWith("image/") ||
                          /\.(png|jpe?g)$/i.test(att.filename ?? "");
                        return (
                          <div key={att.id}>
                            <div className="answer-pdf-link">
                              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                <path d="M14 2v6h6" />
                              </svg>
                              <a href={att.url} target="_blank" rel="noreferrer">
                                {att.filename || "Open attachment"}
                              </a>
                            </div>
                            {isImage && (
                              <div style={{ fontSize: "0.75rem", color: "var(--muted)", marginLeft: 20, marginTop: 2 }}>
                                Image attachment — not summarized
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
                );
              })}
            </div>
            );
          })()}

          {(inquiry.requester_name || inquiry.requester_email) && (
            <div className="detail-section detail-meta-row">
              {inquiry.requester_name && (
                <div>
                  <div className="detail-label">Requester</div>
                  <div>{inquiry.requester_name}</div>
                </div>
              )}
              {inquiry.requester_email && (
                <div>
                  <div className="detail-label">Reply-to</div>
                  <div>{inquiry.requester_email}</div>
                </div>
              )}
              <div>
                <div className="detail-label">Fallback window</div>
                <div>{fmtFallbackStatus(inquiry.manufacturer?.fallback_call_enabled, inquiry.fallback_after_hours, inquiry.manufacturer?.mi_phone)}</div>
              </div>
            </div>
          )}

          {/* Timeline — built as a sorted data array so entries stay chronological
              even with follow-ups after closing; entries with no timestamp sort last. */}
          {(() => {
            const toMs = (s?: string | null): number | null => {
              if (!s) return null;
              const t = new Date(s).getTime();
              return Number.isNaN(t) ? null : t;
            };
            const SORT_LAST = Number.MAX_SAFE_INTEGER;

            type TimelineEntry = {
              key: string;
              sortAt: number;
              status: "done" | "pending";
              title: ReactNode;
              meta?: ReactNode;
              body?: ReactNode; // rendered inside .timeline-body
              extra?: ReactNode; // rendered raw, no wrapper (e.g. transcript disclosure)
            };

            const entries: TimelineEntry[] = [];

            entries.push({
              key: "created",
              sortAt: toMs(inquiry.created_at) ?? 0,
              status: "done",
              title: "Inquiry created",
              meta: fmtDate(inquiry.created_at),
            });

            if (inquiry.email_scheduled_for && !inquiry.email_sent_at) {
              entries.push({
                key: "email-scheduled",
                sortAt: toMs(inquiry.email_scheduled_for) ?? SORT_LAST,
                status: "pending",
                title: "Email scheduled",
                meta: <>Sends at {fmtDate(inquiry.email_scheduled_for)}</>,
              });
            }

            if (isCallScheduled) {
              entries.push({
                key: "call-scheduled",
                sortAt: toMs(inquiry.call_scheduled_for) ?? SORT_LAST,
                status: "pending",
                title: "Call scheduled",
                meta: <>Calls at {fmtDate(inquiry.call_scheduled_for)}</>,
              });
            } else {
              entries.push({
                key: "email-sent",
                sortAt:
                  toMs(inquiry.email_sent_at) ??
                  toMs(inquiry.email_scheduled_for) ??
                  toMs(inquiry.created_at) ??
                  0,
                status: inquiry.email_sent_at ? "done" : "pending",
                title: "Email sent to manufacturer",
                meta: (
                  <>
                    {fmtDate(inquiry.email_sent_at) ?? "—"}
                    {inquiry.call_scheduled_for && !inquiry.email_response_at && (
                      <> · fallback call at {fmtDate(inquiry.call_scheduled_for)}</>
                    )}
                  </>
                ),
              });
            }

            if (inquiry.email_response_at) {
              entries.push({
                key: "email-response",
                sortAt: toMs(inquiry.email_response_at) ?? SORT_LAST,
                status: "done",
                title: "Email response received",
                meta: fmtDate(inquiry.email_response_at),
                body: inquiry.email_response ? renderBold(inquiry.email_response) : undefined,
              });
            }

            (inquiry.email_replies ?? [])
              .filter((reply) => reply.direction === "outbound")
              .forEach((reply) => {
                entries.push({
                  key: `followup-email-${reply.id}`,
                  sortAt: toMs(reply.sent_at) ?? SORT_LAST,
                  status: "done",
                  title: "Follow-up email sent to manufacturer",
                  meta: fmtDate(reply.sent_at),
                  body: reply.body ? renderBold(reply.body) : undefined,
                });
              });

            // One entry per CallLog — each physical call gets its own entry+transcript
            // (see models.CallLog); sorted oldest-first only to number "Follow-up call N".
            const callLogs = [...(inquiry.call_logs ?? [])].sort(
              (a, b) => (toMs(a.started_at) ?? 0) - (toMs(b.started_at) ?? 0)
            );
            // Deep-link target (#...&focus=transcript) opens the most recent call's
            // transcript — only the last transcript-bearing log gets this id.
            const lastTranscriptLogId = [...callLogs].reverse().find((l) => l.transcript)?.id;
            if (callLogs.length > 0) {
              callLogs.forEach((log, idx) => {
                const isFollowup = idx > 0;
                entries.push({
                  key: `call-${log.id}`,
                  sortAt: toMs(log.completed_at) ?? toMs(log.started_at) ?? SORT_LAST,
                  status: log.completed_at ? "done" : "pending",
                  title: log.completed_at
                    ? isFollowup
                      ? "Follow-up call completed"
                      : "Agent call completed"
                    : isFollowup
                    ? "Follow-up call in progress"
                    : "Agent call in progress",
                  meta: fmtDate(log.completed_at) ?? `scheduled ${fmtDate(log.started_at) ?? ""}`,
                  extra: log.transcript ? (
                    <details
                      id={log.id === lastTranscriptLogId ? "call-transcript" : undefined}
                      className="transcript-toggle"
                      open={
                        log.id === lastTranscriptLogId &&
                        typeof window !== "undefined" &&
                        new URLSearchParams(window.location.hash.split("?")[1] || "").get("focus") ===
                          "transcript"
                      }
                    >
                      <summary>View full call transcript</summary>
                      <pre>{log.transcript}</pre>
                    </details>
                  ) : undefined,
                });
              });
            } else if (callInFlight || inquiry.call_completed_at) {
              // Fallback for inquiries with no CallLog rows yet (predates the backfill
              // migration) — mirrors the single-entry behavior this replaced.
              entries.push({
                key: "call",
                sortAt:
                  toMs(inquiry.call_completed_at) ??
                  toMs(inquiry.call_scheduled_for) ??
                  SORT_LAST,
                status: inquiry.call_completed_at ? "done" : "pending",
                title: inquiry.call_completed_at ? "Agent call completed" : "Agent call in progress",
                meta:
                  fmtDate(inquiry.call_completed_at) ??
                  `scheduled ${fmtDate(inquiry.call_scheduled_for) ?? ""}`,
                extra: inquiry.call_transcript ? (
                  <details
                    id="call-transcript"
                    className="transcript-toggle"
                    open={
                      typeof window !== "undefined" &&
                      new URLSearchParams(window.location.hash.split("?")[1] || "").get("focus") ===
                        "transcript"
                    }
                  >
                    <summary>View full call transcript</summary>
                    <pre>{inquiry.call_transcript}</pre>
                  </details>
                ) : undefined,
              });
            }

            if (inquiry.status === "closed") {
              entries.push({
                key: "closed",
                // closed_at is write-once (see close_inquiry); this updated_at fallback only
                // matters for the brief window before the server-side backfill runs.
                sortAt: toMs(inquiry.closed_at) ?? toMs(inquiry.updated_at) ?? SORT_LAST,
                status: "done",
                title: "Closed",
              });
            }

            entries.sort((a, b) => a.sortAt - b.sortAt);

            return (
              <div className="detail-section">
                <div className="detail-label">Timeline</div>
                <ol className="timeline">
                  {entries.map((entry) => (
                    <li key={entry.key} className={`timeline-item ${entry.status}`}>
                      <div className="timeline-dot" />
                      <div>
                        <div className="timeline-title">{entry.title}</div>
                        {entry.meta != null && <div className="timeline-meta">{entry.meta}</div>}
                        {entry.body && <div className="timeline-body">{entry.body}</div>}
                        {entry.extra}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            );
          })()}

          {nextRetry && retryCount < maxRetries && (
            <div className="detail-section retry-banner">
              <strong>Auto-retry scheduled</strong> for{" "}
              {nextRetry.toLocaleString()} ({retryCount}/{maxRetries} retries used).
              The voice agent will dial again automatically. You can also click
              "Trigger Call Now" below to retry immediately.
            </div>
          )}

          {inquiry.status === "needs_attention" && (
            <div className="detail-section retry-banner retry-banner-warn">
              <strong>Not responded after {retryCount} attempt
              {retryCount === 1 ? "" : "s"}.</strong> Decide what to do next —
              retry manually, send the inquiry by email, or close it.
            </div>
          )}

          {isDraft && (
            <div className="detail-section action-panel">
              <div className="detail-label">Draft</div>
              {editingDraft ? (
                <>
                  <label className="detail-label" style={{ marginTop: 8 }}>Subject</label>
                  <input
                    type="text"
                    value={editSubject}
                    onChange={(e) => setEditSubject(e.target.value)}
                    maxLength={1000}
                  />
                  <label className="detail-label" style={{ marginTop: 8 }}>Question</label>
                  <textarea
                    value={editQuestion}
                    onChange={(e) => setEditQuestion(e.target.value)}
                    rows={4}
                  />
                  <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                    <button
                      className="btn btn-primary"
                      type="button"
                      disabled={busy || !editSubject.trim() || !editQuestion.trim()}
                      onClick={() =>
                        run(async () => {
                          await onAction("editDraft", { subject: editSubject, question: editQuestion });
                          setEditingDraft(false);
                        })
                      }
                    >
                      Save Changes
                    </button>
                    <button
                      className="btn btn-ghost"
                      type="button"
                      disabled={busy}
                      onClick={() => setEditingDraft(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  {preferredChannel === "email" && emailReachable && (
                    <button
                      className="btn btn-primary"
                      type="button"
                      disabled={busy}
                      onClick={() => run(() => onAction("sendEmail"))}
                    >
                      Send Email
                    </button>
                  )}
                  {preferredChannel === "webform" && webFormReachable && (
                    <button
                      className="btn btn-ghost"
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        window.open(inquiry.manufacturer!.mi_web_form_url!, "_blank", "noopener,noreferrer")
                      }
                    >
                      Open Web Form
                    </button>
                  )}
                  {preferredChannel === "call" && callReachable && (
                    <span className="cell-muted" style={{ alignSelf: "center" }}>
                      This manufacturer prefers Call — use "Trigger Call Now" below.
                    </span>
                  )}
                  <button
                    className="btn btn-ghost"
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEditSubject(inquiry.subject);
                      setEditQuestion(inquiry.question);
                      setEditingDraft(true);
                    }}
                  >
                    Edit
                  </button>
                </div>
              )}
              {!editingDraft && preferredChannel === "email" && !emailReachable && (
                <div className="cell-muted" style={{ marginTop: 8 }}>
                  Preferred channel is Email, but no email address is on file — cannot send.
                </div>
              )}
              {!editingDraft && preferredChannel === "call" && !callReachable && (
                <div className="cell-muted" style={{ marginTop: 8 }}>
                  Preferred channel is Call, but no phone number is on file — cannot call.
                </div>
              )}
              {!editingDraft && preferredChannel === "webform" && !webFormReachable && (
                <div className="cell-muted" style={{ marginTop: 8 }}>
                  Preferred channel is Web Form, but no web form URL is on file.
                </div>
              )}
              {!editingDraft && preferredChannel === "unsupported" && (
                <div className="cell-muted" style={{ marginTop: 8 }}>
                  {m?.manufacturer ?? "This manufacturer"} has no supported outreach method in
                  this app{m?.preferred_channel ? ` (preferred: ${m.preferred_channel})` : " (no preferred channel set)"}.
                </div>
              )}
            </div>
          )}

          {/* Call scheduled for next business hours — read-only, no draft/email actions */}
          {isCallScheduled && (
            <div className="detail-section action-panel">
              <div className="detail-label">Call Scheduled</div>
              <div className="cell-muted" style={{ marginTop: 8 }}>
                Scheduled for {fmtDate(inquiry.call_scheduled_for)}
              </div>
            </div>
          )}

          {isScheduled && (
            <div className="detail-section action-panel">
              <div className="detail-label">
                Scheduled email
                {inquiry.email_scheduled_for && (
                  <span className="timeline-meta" style={{ marginLeft: 8 }}>
                    · sends at {new Date(inquiry.email_scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}{" "}
                    ({new Date(inquiry.email_scheduled_for).toLocaleDateString()})
                  </span>
                )}
              </div>

              {editingScheduled ? (
                <>
                  <label className="detail-label" style={{ marginTop: 8 }}>Subject</label>
                  <input
                    type="text"
                    value={editSubject}
                    onChange={(e) => setEditSubject(e.target.value)}
                    maxLength={1000}
                  />
                  <label className="detail-label" style={{ marginTop: 8 }}>Question</label>
                  <textarea
                    value={editQuestion}
                    onChange={(e) => setEditQuestion(e.target.value)}
                    rows={4}
                  />
                  <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                    <button
                      className="btn btn-primary"
                      type="button"
                      disabled={busy || !editSubject.trim() || !editQuestion.trim()}
                      onClick={() =>
                        run(async () => {
                          await onAction("editScheduledEmail", { subject: editSubject, question: editQuestion });
                          setEditingScheduled(false);
                        })
                      }
                    >
                      Save Changes
                    </button>
                    <button
                      className="btn btn-ghost"
                      type="button"
                      disabled={busy}
                      onClick={() => setEditingScheduled(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                  <button
                    className="btn btn-primary"
                    type="button"
                    disabled={busy}
                    onClick={() => run(() => onAction("sendNow"))}
                  >
                    Send Now
                  </button>
                  <button
                    className="btn btn-ghost"
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEditSubject(inquiry.subject);
                      setEditQuestion(inquiry.question);
                      setEditingScheduled(true);
                    }}
                  >
                    Edit Content
                  </button>
                  <button
                    className="btn btn-ghost-danger"
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      if (confirm("Cancel the scheduled email and revert to draft?")) {
                        run(() => onAction("cancelScheduledEmail"));
                      }
                    }}
                  >
                    Cancel Send
                  </button>
                </div>
              )}
            </div>
          )}

          {canRecordEmail && (
            <div className="detail-section action-panel">
              <div className="detail-label">Log email response</div>
              <textarea
                value={emailReply}
                onChange={(e) => setEmailReply(e.target.value)}
                rows={3}
                placeholder="Paste the manufacturer's email reply…"
              />
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy || !emailReply.trim()}
                onClick={() =>
                  run(async () => {
                    await onAction("recordEmailResponse", emailReply);
                    setEmailReply("");
                  })
                }
              >
                Save Email Response
              </button>
            </div>
          )}

          {canRecordCall && (
            <div className="detail-section action-panel">
              <div className="detail-label">Log call result (agent)</div>
              <input
                type="text"
                value={callSummary}
                onChange={(e) => setCallSummary(e.target.value)}
                placeholder="One-line summary"
              />
              <textarea
                value={callTranscript}
                onChange={(e) => setCallTranscript(e.target.value)}
                rows={4}
                placeholder="Optional: full call transcript"
              />
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy || !callSummary.trim()}
                onClick={() =>
                  run(async () => {
                    await onAction("recordCallResult", {
                      summary: callSummary,
                      transcript: callTranscript,
                    });
                    setCallSummary("");
                    setCallTranscript("");
                  })
                }
              >
                Save Call Result
              </button>
            </div>
          )}

          {/* Always available, regardless of status — including closed and
              completed inquiries. The user can always follow up. */}
          <div className="detail-section action-panel">
            <div className="detail-label">Contact Manufacturer Again</div>
            {isResolvedStatus && (
              <div className="cell-muted" style={{ marginBottom: 8 }}>
                This inquiry is {inquiry.status === "closed" ? "closed" : "resolved"} — a follow-up
                email or call will not change that.
              </div>
            )}

            <label className="detail-label" style={{ marginTop: 4 }}>
              {isCallScheduled ? "Send Email" : "Send Another Email"}
            </label>
            {emailReachable ? (
              <>
                <textarea
                  value={followupBody}
                  onChange={(e) => setFollowupBody(e.target.value)}
                  rows={3}
                  placeholder="Type a follow-up message to send to the manufacturer…"
                  disabled={busy}
                />
                <div style={{ marginTop: 8 }}>
                  <button
                    className="btn btn-primary"
                    type="button"
                    disabled={busy || !followupBody.trim()}
                    onClick={() =>
                      run(async () => {
                        await onAction("sendFollowupEmail", { body: followupBody });
                        setFollowupBody("");
                      })
                    }
                  >
                    Send Email
                  </button>
                </div>
              </>
            ) : (
              <div className="cell-muted">No email address on file for this manufacturer.</div>
            )}

            <label className="detail-label" style={{ marginTop: 16 }}>Call</label>
            {callReachable ? (
              <div style={{ marginTop: 4 }}>
                <button
                  className="btn btn-ghost"
                  type="button"
                  disabled={busy || !canTriggerCall}
                  onClick={() => run(() => onAction("triggerCall"))}
                >
                  {retryButtonLabel}
                </button>
              </div>
            ) : (
              <div className="cell-muted">No phone number on file for this manufacturer.</div>
            )}
          </div>
        </div>

        <div className="modal-footer modal-footer-split">
          <button
            type="button"
            className="btn btn-link btn-link-danger"
            onClick={() =>
              run(async () => {
                if (confirm("Delete this inquiry?")) await onDelete();
              })
            }
            disabled={busy}
          >
            Delete
          </button>
          <div className="footer-actions">
            {inquiry.status === "call_completed" && inquiry.call_transcript && !inquiry.final_answer && (
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy}
                onClick={() => run(() => onAction("extractAnswer"))}
              >
                Extract Answer
              </button>
            )}
            {inquiry.status === "needs_attention" && (
              <button
                className="btn btn-ghost"
                type="button"
                disabled={busy}
                onClick={() => {
                  if (confirm("Reset retry count and return this inquiry to draft?")) {
                    run(() => onAction("resetRetries"));
                  }
                }}
              >
                Reset Retries
              </button>
            )}
            {canClose && (
              <button
                className="btn btn-ghost"
                type="button"
                disabled={busy}
                onClick={() => run(async () => { if (confirm("Close this inquiry?")) await onAction("close"); })}
              >
                Close Inquiry
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default InquiryDetail;
