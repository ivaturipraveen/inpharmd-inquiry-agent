import { FC, useEffect, useState } from "react";
import StatusBadge from "./StatusBadge";
import type { Inquiry } from "../types";
import { renderBold } from "../utils/renderBold";

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

  const callInFlight = inquiry.status === "call_pending";
  const isDraft = inquiry.status === "draft";
  const isScheduled = inquiry.status === "email_pending";
  const canRecordEmail = inquiry.status === "email_sent";
  // Test call inquiries must never trigger a production manufacturer call.
  const canTriggerCall =
    !isTestCall &&
    (callInFlight ||
      ["email_sent", "draft", "needs_attention"].includes(inquiry.status) ||
      (inquiry.status === "call_completed" &&
        inquiry.call_provider_status !== "answered" &&
        inquiry.call_provider_status !== "follow_up_via_email"));
  const canRecordCall = inquiry.status === "call_pending" || inquiry.status === "needs_attention";
  const canClose = !["closed"].includes(inquiry.status);

  const retryCount = inquiry.retry_count ?? 0;
  const maxRetries = inquiry.max_retries ?? 2;
  const nextRetry = inquiry.next_retry_at ? new Date(inquiry.next_retry_at) : null;
  const retryButtonLabel = callInFlight
    ? "Call in progress…"
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
          </div>

          {/* Final Answer shows automatically once the agent or AI has captured one.
              When the reply included an attachment, we surface three distinct pieces
              top-to-bottom: the manufacturer's direct response, the AI summary
              of the attachment, and the link to download it. */}
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

          {/* Manufacturer Email Thread — all inbound replies in chronological order.
              Only rendered when email_replies data is available (new records).
              The Final Answer box above is untouched. */}
          {(inquiry.email_replies?.length ?? 0) > 0 && (
            <div className="detail-section">
              <div className="detail-label">Manufacturer Email Thread</div>
              {inquiry.email_replies!.map((reply, idx) => (
                <div
                  key={reply.id}
                  style={{
                    borderTop: idx > 0 ? "1px solid var(--line)" : undefined,
                    paddingTop: idx > 0 ? "12px" : "8px",
                    marginTop: idx > 0 ? "12px" : "4px",
                  }}
                >
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline", marginBottom: 6, flexWrap: "wrap" }}>
                    <span style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--brand-orange)" }}>
                      Reply {idx + 1}
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
              ))}
            </div>
          )}

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
                <div>{inquiry.fallback_after_hours} hours</div>
              </div>
            </div>
          )}

          {/* Timeline */}
          <div className="detail-section">
            <div className="detail-label">Timeline</div>
            <ol className="timeline">
              <li className="timeline-item done">
                <div className="timeline-dot" />
                <div>
                  <div className="timeline-title">Inquiry created</div>
                  <div className="timeline-meta">{fmtDate(inquiry.created_at)}</div>
                </div>
              </li>

              {inquiry.email_scheduled_for && !inquiry.email_sent_at && (
                <li className="timeline-item pending">
                  <div className="timeline-dot" />
                  <div>
                    <div className="timeline-title">Email scheduled</div>
                    <div className="timeline-meta">
                      Sends at {fmtDate(inquiry.email_scheduled_for)}
                    </div>
                  </div>
                </li>
              )}

              <li
                className={`timeline-item ${
                  inquiry.email_sent_at ? "done" : "pending"
                }`}
              >
                <div className="timeline-dot" />
                <div>
                  <div className="timeline-title">Email sent to manufacturer</div>
                  <div className="timeline-meta">
                    {fmtDate(inquiry.email_sent_at) ?? "—"}
                    {inquiry.call_scheduled_for && !inquiry.email_response_at && (
                      <> · fallback call at {fmtDate(inquiry.call_scheduled_for)}</>
                    )}
                  </div>
                </div>
              </li>

              {inquiry.email_response_at && (
                <li className="timeline-item done">
                  <div className="timeline-dot" />
                  <div>
                    <div className="timeline-title">Email response received</div>
                    <div className="timeline-meta">
                      {fmtDate(inquiry.email_response_at)}
                    </div>
                    {inquiry.email_response && (
                      <div className="timeline-body">{renderBold(inquiry.email_response)}</div>
                    )}
                  </div>
                </li>
              )}

              {(inquiry.status === "call_pending" || inquiry.call_completed_at) && (
                <li
                  className={`timeline-item ${
                    inquiry.call_completed_at ? "done" : "pending"
                  }`}
                >
                  <div className="timeline-dot" />
                  <div>
                    <div className="timeline-title">
                      {inquiry.call_completed_at
                        ? "Agent call completed"
                        : "Agent call in progress"}
                    </div>
                    <div className="timeline-meta">
                      {fmtDate(inquiry.call_completed_at) ??
                        `scheduled ${fmtDate(inquiry.call_scheduled_for) ?? ""}`}
                    </div>
                    {inquiry.call_transcript && (
                      <details
                        id="call-transcript"
                        className="transcript-toggle"
                        open={
                          typeof window !== "undefined" &&
                          new URLSearchParams(
                            window.location.hash.split("?")[1] || ""
                          ).get("focus") === "transcript"
                        }
                      >
                        <summary>View full call transcript</summary>
                        <pre>{inquiry.call_transcript}</pre>
                      </details>
                    )}
                  </div>
                </li>
              )}

              {inquiry.status === "closed" && (
                <li className="timeline-item done">
                  <div className="timeline-dot" />
                  <div>
                    <div className="timeline-title">Closed</div>
                  </div>
                </li>
              )}
            </ol>
          </div>

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

          {/* Draft actions */}
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
                  <button
                    className="btn btn-primary"
                    type="button"
                    disabled={busy}
                    onClick={() => run(() => onAction("sendEmail"))}
                  >
                    Send Email
                  </button>
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
            </div>
          )}

          {/* Scheduled email actions */}
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

          {/* Action panels */}
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
            {canTriggerCall && (
              <button
                className={
                  inquiry.status === "needs_attention"
                    ? "btn btn-primary"
                    : "btn btn-ghost"
                }
                type="button"
                disabled={busy || callInFlight}
                onClick={() => run(() => onAction("triggerCall"))}
              >
                {retryButtonLabel}
              </button>
            )}
            {canClose && (
              <button
                className="btn btn-ghost"
                type="button"
                disabled={busy}
                onClick={() => run(() => onAction("close"))}
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
