import { FC, useEffect, useState } from "react";
import { isWithinBusinessHoursNow } from "../utils/businessHours";
import { bucketByPreferredChannel } from "../utils/channelResolution";
import { shouldShowTriggerAll } from "../utils/triggerAllVisibility";
import type { ManufacturerContact, WebFormAutomationResult } from "../types";

interface Props {
  manufacturers: ManufacturerContact[];
  fallbackHours: number;
  /** True when the selected manufacturers' fallback times are not all the
   *  same — shows a generic "configured individually" message instead of a
   *  single number that would otherwise misrepresent the other values. */
  fallbackHoursVaries?: boolean;
  /** Shown in the modal header when the inquiry already exists (e.g. "Inquiry #5 created").
   *  Omit for the deferred-create flow where no inquiry exists yet. */
  inquiryLabel?: string;
  onSendEmail: () => Promise<void>;
  onCallAgent: () => Promise<void>;
  /** Dispatches Email-eligible and Call-eligible manufacturers together. */
  onTriggerAll?: () => Promise<void>;
  /** Called when user dismisses via ×, Escape, or backdrop. Nothing is created. */
  onClose: () => void;
  /** Fills and submits the real manufacturer form; omit to hide this section. */
  onSubmitWebForm?: (manufacturerId: number) => Promise<WebFormAutomationResult>;
  /** Called only when the standalone Submit Web Form button (not Trigger All)
   *  finishes with every submission a real automation_success. */
  onWebFormFinished?: () => void;
}

const ChannelChooser: FC<Props> = ({
  manufacturers,
  fallbackHours,
  fallbackHoursVaries = false,
  inquiryLabel,
  onSendEmail,
  onCallAgent,
  onTriggerAll,
  onClose,
  onSubmitWebForm,
  onWebFormFinished,
}) => {
  const [busy, setBusy] = useState<"email" | "call" | "all" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Keyed by manufacturer id so state never crosses between manufacturers.
  const [webFormBusy, setWebFormBusy] = useState<Record<number, "prepare" | "submit">>({});
  const [webFormResults, setWebFormResults] = useState<Record<number, WebFormAutomationResult>>({});
  const [webFormErrors, setWebFormErrors] = useState<Record<number, string>>({});

  const m = manufacturers[0];
  const isMulti = manufacturers.length > 1;

  // Each manufacturer's own preferred_channel decides which card applies —
  // never inferred from which contact fields happen to be populated.
  const buckets = bucketByPreferredChannel(manufacturers);

  const emailEligibleCount = buckets.email.length;
  const callEligibleCount = buckets.call.length;
  const inHours = isWithinBusinessHoursNow(m?.mi_phone_hours);
  const outOfHours = inHours === false;

  const webFormManufacturers = buckets.webform as (ManufacturerContact & { mi_web_form_url: string })[];
  const webFormCapableCount = webFormManufacturers.length;

  // Manufacturers with a missing required field, or no supported outreach
  // mechanism at all — surfaced explicitly, never silently reassigned.
  const attentionItems: { name: string; reason: string }[] = [
    ...buckets.emailUnreachable.map(x => ({
      name: x.manufacturer,
      reason: "prefers Email but has no email address on file",
    })),
    ...buckets.callUnreachable.map(x => ({
      name: x.manufacturer,
      reason: "prefers Call but has no phone number on file",
    })),
    ...buckets.webformUnreachable.map(x => ({
      name: x.manufacturer,
      reason: "prefers Web Form but has no web form URL on file",
    })),
    ...buckets.unsupported.map(x => ({
      name: x.manufacturer,
      reason: x.preferred_channel
        ? `has no supported outreach method in this app (preferred: ${x.preferred_channel})`
        : "has no preferred channel set on file",
    })),
  ];

  // Out-of-hours no longer disables Call — onCallAgent schedules it instead.
  const callDisabled = callEligibleCount === 0 || busy !== null;

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);

  // Escape key closes without creating anything.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && busy === null) onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [busy, onClose]);

  const handleEmail = async () => {
    setBusy("email");
    setError(null);
    try {
      await onSendEmail();
    } catch (e: any) {
      setError(e?.message ?? "Failed to send email.");
      setBusy(null);
    }
  };

  const handleCall = async () => {
    setBusy("call");
    setError(null);
    try {
      await onCallAgent();
    } catch (e: any) {
      const msg = e?.message ?? "Failed to place call.";
      setError(
        msg.includes("503")
          ? "ElevenLabs is not configured yet. Add ELEVENLABS_API_KEY / " +
              "ELEVENLABS_AGENT_ID / ELEVENLABS_AGENT_PHONE_NUMBER_ID to backend/.env and restart."
          : msg,
      );
      setBusy(null);
    }
  };

  const submitWebFormForOne = async (manufacturerId: number): Promise<boolean> => {
    if (!onSubmitWebForm) return false;
    setWebFormBusy((prev) => ({ ...prev, [manufacturerId]: "submit" }));
    setWebFormErrors((prev) => { const next = { ...prev }; delete next[manufacturerId]; return next; });
    try {
      const result = await onSubmitWebForm(manufacturerId);
      setWebFormResults((prev) => ({ ...prev, [manufacturerId]: result }));
      return result.outcome === "automation_success";
    } catch (e: any) {
      setWebFormErrors((prev) => ({ ...prev, [manufacturerId]: e?.message ?? "Failed to submit the Web Form." }));
      return false;
    } finally {
      setWebFormBusy((prev) => { const next = { ...prev }; delete next[manufacturerId]; return next; });
    }
  };

  // Sequential per manufacturer so each result/error state updates as it goes.
  // Already-succeeded manufacturers are skipped — a retry only redoes failures.
  // Returns whether every manufacturer ended at automation_success, for the
  // standalone button's finish behavior (Trigger All ignores this return value).
  const handleSubmitWebForm = async (): Promise<boolean> => {
    let allSucceeded = true;
    for (const wm of webFormManufacturers) {
      if (webFormResults[wm.id]?.outcome === "automation_success") continue;
      const ok = await submitWebFormForOne(wm.id);
      if (!ok) allSucceeded = false;
    }
    return allSucceeded;
  };

  // Only the standalone button reaches this — Trigger All calls
  // handleSubmitWebForm directly and ignores its return value.
  const handleSubmitWebFormStandalone = async () => {
    const allSucceeded = await handleSubmitWebForm();
    if (allSucceeded) onWebFormFinished?.();
  };

  const showTriggerAll = shouldShowTriggerAll({
    hasTriggerAllHandler: !!onTriggerAll,
    emailEligibleCount,
    callEligibleCount,
    webFormCapableCount,
    hasWebFormHandler: !!onSubmitWebForm,
  });

  const handleTriggerAll = async () => {
    setBusy("all");
    setError(null);
    const hasUnfinishedWebForm = webFormManufacturers.some(
      (wm) => webFormResults[wm.id]?.outcome !== "automation_success",
    );
    // Web Form first, while the modal is still open, so results are visible.
    if (onSubmitWebForm && hasUnfinishedWebForm) {
      await handleSubmitWebForm();
    }
    try {
      await onTriggerAll?.();
    } catch (e: any) {
      setError(e?.message ?? "Failed to trigger all channels.");
      setBusy(null);
    }
  };

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
            {inquiryLabel && <div className="meta-text">{inquiryLabel}</div>}
            <h2>How should we reach {isMulti ? `these ${manufacturers.length} manufacturers` : (m?.manufacturer ?? "the manufacturer")}?</h2>
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
          {error && <div className="error-banner">{error}</div>}

          <div className="channel-grid">
            {/* Email card — only manufacturers whose preferred_channel is Email */}
            <div className={`channel-card ${emailEligibleCount === 0 ? "channel-disabled" : ""}`}>
              <div className="channel-icon channel-icon-email">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="5" width="18" height="14" rx="2" />
                  <path d="m3 7 9 6 9-6" />
                </svg>
              </div>
              <div className="channel-title">Send Email</div>
              <div className="channel-sub">
                {isMulti ? (
                  emailEligibleCount > 0 ? (
                    <>
                      <strong>{emailEligibleCount}</strong>{" "}
                      {emailEligibleCount === 1 ? "manufacturer prefers" : "manufacturers prefer"} Email
                      and will be emailed. Voice agent will call any that don't reply
                      {fallbackHoursVaries
                        ? " — fallback times are configured individually per eligible manufacturer."
                        : <> within <strong>{fallbackHours}h</strong>.</>}
                    </>
                  ) : (
                    "None of the selected manufacturers prefer Email."
                  )
                ) : emailEligibleCount > 0 ? (
                  <>
                    We'll send to <strong>{m?.official_mi_email || m?.team_verified_email}</strong> and wait{" "}
                    <strong>{fallbackHours}h</strong> for a reply before the
                    voice agent calls.
                  </>
                ) : (
                  "This manufacturer's preferred channel is not Email."
                )}
              </div>
              <ul className="channel-meta">
                <li>
                  <span>SLA</span> {isMulti ? "—" : (m?.typical_response_sla ?? "—")}
                </li>
                <li>
                  <span>Fallback</span>{" "}
                  {isMulti && fallbackHoursVaries
                    ? "Configured individually per eligible manufacturer"
                    : `Agent call after ${fallbackHours}h`}
                </li>
              </ul>
              <button
                className="btn btn-primary"
                type="button"
                disabled={emailEligibleCount === 0 || busy !== null}
                onClick={handleEmail}
              >
                {busy === "email" ? "Sending…" : "Send Email"}
              </button>
            </div>

            {/* Call card — only manufacturers whose preferred_channel is Phone */}
            <div className={`channel-card ${callEligibleCount === 0 ? "channel-disabled" : ""}`}>
              <div className="channel-icon channel-icon-call">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.86 19.86 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.86 19.86 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92Z" />
                </svg>
              </div>
              <div className="channel-title">Call Agent Now</div>
              <div className="channel-sub">
                {isMulti ? (
                  callEligibleCount > 0 ? (
                    <>
                      <strong>{callEligibleCount}</strong>{" "}
                      {callEligibleCount === 1 ? "manufacturer prefers" : "manufacturers prefer"} Call
                      and will be called.
                    </>
                  ) : (
                    "None of the selected manufacturers prefer Call."
                  )
                ) : callEligibleCount > 0 ? (
                  <>
                    Voice agent will dial <strong>{m?.mi_phone}</strong> and ask
                    the question on your behalf.
                  </>
                ) : (
                  "This manufacturer's preferred channel is not Call."
                )}
              </div>
              <ul className="channel-meta">
                {isMulti ? (
                  <li>
                    <span>Preferred Call</span> {callEligibleCount} of {manufacturers.length}
                  </li>
                ) : (
                  <>
                    <li>
                      <span>Hours</span>{" "}
                      {m?.mi_phone_hours ?? m?.typical_response_sla ?? "—"}
                    </li>
                    <li>
                      <span>Status</span>{" "}
                      {inHours === null ? (
                        <em className="warn">unknown</em>
                      ) : inHours ? (
                        <em className="ok">In business hours now</em>
                      ) : (
                        <em className="warn" style={{ textAlign: "right" }}>
                          Outside business hours
                          <br />
                          (will be scheduled)
                        </em>
                      )}
                    </li>
                  </>
                )}
              </ul>
              <button
                className="btn btn-primary"
                type="button"
                disabled={callDisabled}
                title={
                  !isMulti && outOfHours
                    ? `Outside ${m?.manufacturer ?? "manufacturer"} business hours (${m?.mi_phone_hours ?? "unknown"}) — will be scheduled instead.`
                    : undefined
                }
                onClick={handleCall}
              >
                {busy === "call"
                  ? (!isMulti && outOfHours ? "Scheduling…" : "Dialing…")
                  : !isMulti && outOfHours
                  ? "Schedule Call"
                  : "Call Agent Now"}
              </button>
            </div>

            {/* Web Form card — single action, fills and submits directly. */}
            <div className={`channel-card ${webFormCapableCount === 0 ? "channel-disabled" : ""}`}>
              <div className="channel-icon channel-icon-test">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                  <path d="M9 15h6" />
                  <path d="M9 11h6" />
                </svg>
              </div>
              <div className="channel-title">{isMulti ? "Submit Web Forms" : "Submit Web Form"}</div>
              <div className="channel-sub">
                {isMulti ? (
                  webFormCapableCount > 0 ? (
                    <>
                      <strong>{webFormCapableCount}</strong>{" "}
                      {webFormCapableCount === 1 ? "manufacturer prefers" : "manufacturers prefer"} Web
                      Form and will have their form filled and submitted. If human verification
                      (CAPTCHA, Cloudflare, etc.) is detected for one, it's marked Needs Attention
                      instead — nothing is auto-submitted for a manufacturer that requires a human.
                    </>
                  ) : (
                    "None of the selected manufacturers prefer Web Form."
                  )
                ) : webFormCapableCount > 0 ? (
                  <>
                    Fills and submits this manufacturer's medical information request form using
                    this inquiry's data. If human verification is detected, it's marked Needs
                    Attention instead of being auto-submitted.
                  </>
                ) : (
                  "This manufacturer's preferred channel is not Web Form."
                )}
              </div>
              <button
                className="btn btn-primary"
                type="button"
                disabled={!onSubmitWebForm || webFormCapableCount === 0 || busy !== null || Object.keys(webFormBusy).length > 0}
                onClick={handleSubmitWebFormStandalone}
              >
                {Object.keys(webFormBusy).length > 0
                  ? "Submitting…"
                  : isMulti
                  ? "Submit Web Forms"
                  : "Submit Web Form"}
              </button>
            </div>
          </div>

          {webFormManufacturers.some((wm) => webFormResults[wm.id] || webFormErrors[wm.id]) && (
            <div className="channel-attention webform-automation-poc">
              <div className="detail-label">Web Form results</div>
              {webFormManufacturers.map((wm) => {
                const result = webFormResults[wm.id];
                const err = webFormErrors[wm.id];
                if (!result && !err) return null;
                return (
                  <div key={wm.id} className="webform-automation-row">
                    {isMulti && (
                      <div className="webform-automation-row-header">
                        <strong>{wm.manufacturer}</strong>
                      </div>
                    )}
                    {err && <div className="error-banner">{err}</div>}
                    {result && (
                      result.outcome === "human_action_required" ? (
                        <div className="webform-human-action-banner">
                          <strong>🖐 Manual action required — Open Web Form</strong>
                          <p>
                            {result.reason}
                            {result.mechanism ? ` (${result.mechanism})` : ""} This was marked
                            Needs Attention — automation was stopped before anything was
                            submitted
                            {wm.mi_web_form_url ? ". " : "."}
                            {wm.mi_web_form_url && (
                              <a href={wm.mi_web_form_url} target="_blank" rel="noopener noreferrer">
                                Open the Web Form
                              </a>
                            )}
                            {wm.mi_web_form_url && " to complete it manually."}
                          </p>
                        </div>
                      ) : result.outcome === "submitted_but_unverified" ? (
                        <div className="webform-human-action-banner">
                          <strong>⚠️ Submission outcome could not be verified</strong>
                          <p>
                            {result.reason} This was marked Needs Attention —
                            <strong> do not resubmit</strong> without checking whether the
                            original submission already went through
                            {wm.mi_web_form_url ? ". " : "."}
                            {wm.mi_web_form_url && (
                              <a href={wm.mi_web_form_url} target="_blank" rel="noopener noreferrer">
                                Open the Web Form
                              </a>
                            )}
                            {wm.mi_web_form_url && " to check."}
                          </p>
                        </div>
                      ) : (
                        <div className="cell-muted" style={{ marginTop: 4 }}>
                          <strong>{result.outcome === "automation_success" ? "✓ Submitted — " : "✗ Not submitted — "}</strong>
                          {result.reason}
                        </div>
                      )
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {attentionItems.length > 0 && (
            <div className="channel-attention">
              <div className="detail-label">Needs attention</div>
              <ul className="channel-meta">
                {attentionItems.map((item, idx) => (
                  <li key={idx}>
                    <strong>{item.name}</strong> {item.reason}.
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy !== null}>
            Cancel
          </button>
          {showTriggerAll && (
            <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={handleTriggerAll}>
              {busy === "all" ? "Triggering…" : "Trigger All"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default ChannelChooser;
