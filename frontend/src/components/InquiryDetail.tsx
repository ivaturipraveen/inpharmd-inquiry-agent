import { FC, useState } from "react";
import StatusBadge from "./StatusBadge";
import type { Inquiry } from "../types";

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

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  };

  const m = inquiry.manufacturer;

  const canSendEmail = ["draft", "failed"].includes(inquiry.status);
  const canRecordEmail = inquiry.status === "email_sent";
  // Allow manual call from any not-yet-answered state. Retries reuse this same trigger.
  const canTriggerCall =
    ["email_sent", "draft", "needs_attention"].includes(inquiry.status) ||
    (inquiry.status === "call_completed" &&
      inquiry.call_provider_status !== "answered" &&
      inquiry.call_provider_status !== "follow_up_via_email");
  const canRecordCall = inquiry.status === "call_pending";
  const canClose = !["closed"].includes(inquiry.status);

  const retryCount = inquiry.retry_count ?? 0;
  const maxRetries = inquiry.max_retries ?? 2;
  const nextRetry = inquiry.next_retry_at ? new Date(inquiry.next_retry_at) : null;
  const retryButtonLabel =
    inquiry.status === "needs_attention"
      ? "Retry Call Manually"
      : retryCount > 0
      ? `Trigger Call (retried ${retryCount}/${maxRetries})`
      : "Trigger Call Now";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="detail-head-meta">
              <StatusBadge status={inquiry.status} />
              <span className="meta-dot">·</span>
              <span className="meta-text">
                Inquiry #{inquiry.id} · created {fmtDate(inquiry.created_at)}
              </span>
            </div>
            <h2>{inquiry.subject}</h2>
            {m && (
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
          {/* ANSWER FIRST — most important info at the top */}
          {inquiry.final_answer ? (
            <div className="detail-section answer-box answer-box-prominent">
              <div className="answer-label">
                <span className="answer-icon">✓</span>
                Final Answer
              </div>
              <div className="detail-prose">{inquiry.final_answer}</div>
            </div>
          ) : inquiry.call_transcript ? (
            <div className="detail-section answer-pending">
              <div className="answer-label">No structured answer yet</div>
              <div className="answer-pending-body">
                The agent didn't capture a clean answer via the <code>submit_answer</code> tool,
                but a full transcript is available below. You can extract the answer with AI
                or paste a manual summary.
              </div>
              <button
                className="btn btn-primary btn-sm"
                type="button"
                disabled={busy}
                onClick={() => run(() => onAction("extractAnswer"))}
              >
                ✨ Extract Answer with AI
              </button>
            </div>
          ) : null}

          <div className="detail-section">
            <div className="detail-label">Question</div>
            <div className="detail-prose">{inquiry.question}</div>
          </div>

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
                      <div className="timeline-body">{inquiry.email_response}</div>
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
                      <details className="transcript-toggle">
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
            {canSendEmail && (
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy}
                onClick={() => run(() => onAction("sendEmail"))}
              >
                Mark Email Sent
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
                disabled={busy}
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
