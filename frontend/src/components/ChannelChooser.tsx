import { FC, useEffect, useState } from "react";
import { isWithinBusinessHoursNow } from "../utils/businessHours";
import type { ManufacturerContact } from "../types";

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
  /** Called when user explicitly clicks "Save as Draft". Creates the inquiry. */
  onSaveDraft: () => Promise<void>;
  /** Called when user dismisses via ×, Escape, or backdrop. Nothing is created. */
  onClose: () => void;
}

const ChannelChooser: FC<Props> = ({
  manufacturers,
  fallbackHours,
  fallbackHoursVaries = false,
  inquiryLabel,
  onSendEmail,
  onCallAgent,
  onSaveDraft,
  onClose,
}) => {
  const [busy, setBusy] = useState<"email" | "call" | "draft" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const m = manufacturers[0];
  const isMulti = manufacturers.length > 1;

  const emailTarget = m?.official_mi_email || m?.team_verified_email;
  const phoneTarget = m?.mi_phone;
  const inHours = isWithinBusinessHoursNow(m?.mi_phone_hours);
  const outOfHours = inHours === false;

  const emailCapableCount = isMulti
    ? manufacturers.filter(x => x.official_mi_email || x.team_verified_email).length
    : 0;
  const callCapableCount = isMulti
    ? manufacturers.filter(x => x.mi_phone).length
    : 0;
  const emailDraftCount = isMulti ? manufacturers.length - emailCapableCount : 0;
  const callDraftCount = isMulti ? manufacturers.length - callCapableCount : 0;

  // Every manufacturer with a web form URL. Kept per-manufacturer (not
  // deduped) so each can be listed and opened individually — most browsers
  // block every window.open() after the first one triggered by a single
  // click, so a bulk "open all" button can silently drop later tabs.
  const webFormManufacturers = manufacturers.filter(
    (x): x is ManufacturerContact & { mi_web_form_url: string } => !!x.mi_web_form_url,
  );
  const webFormCapableCount = webFormManufacturers.length;
  const webFormUrls = Array.from(new Set(webFormManufacturers.map(x => x.mi_web_form_url)));
  const webFormLabel = webFormCapableCount === 1 ? "Open Web Form" : "Open Web Forms";

  const callDisabled = isMulti
    ? callCapableCount === 0 || busy !== null
    : !phoneTarget || busy !== null || outOfHours;

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
    if (!isMulti && outOfHours) return;
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

  const handleOpenWebForm = () => {
    webFormUrls.forEach((url) => window.open(url, "_blank", "noopener,noreferrer"));
  };

  const handleSaveDraft = async () => {
    setBusy("draft");
    setError(null);
    try {
      await onSaveDraft();
    } catch (e: any) {
      setError(e?.message ?? "Failed to save draft.");
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
            {/* Email card */}
            <div className={`channel-card ${(isMulti ? emailCapableCount === 0 : !emailTarget) ? "channel-disabled" : ""}`}>
              <div className="channel-icon channel-icon-email">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="5" width="18" height="14" rx="2" />
                  <path d="m3 7 9 6 9-6" />
                </svg>
              </div>
              <div className="channel-title">Send Email</div>
              <div className="channel-sub">
                {isMulti ? (
                  <>
                    <strong>{emailCapableCount}</strong>{" "}
                    {emailCapableCount === 1 ? "manufacturer" : "manufacturers"} will be emailed
                    {emailDraftCount > 0 && (
                      <> · <strong>{emailDraftCount}</strong> will become drafts (no email on file)</>
                    )}. Voice agent will call any that don't reply
                    {fallbackHoursVaries
                      ? " — fallback times are configured individually per eligible manufacturer."
                      : <> within <strong>{fallbackHours}h</strong>.</>}
                  </>
                ) : emailTarget ? (
                  <>
                    We'll send to <strong>{emailTarget}</strong> and wait{" "}
                    <strong>{fallbackHours}h</strong> for a reply before the
                    voice agent calls.
                  </>
                ) : (
                  "No email on file for this manufacturer."
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
                disabled={(isMulti ? emailCapableCount === 0 : !emailTarget) || busy !== null}
                onClick={handleEmail}
              >
                {busy === "email" ? "Sending…" : "Send Email"}
              </button>
            </div>

            {/* Call card */}
            <div className={`channel-card ${(isMulti ? callCapableCount === 0 : !phoneTarget) ? "channel-disabled" : ""}`}>
              <div className="channel-icon channel-icon-call">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.86 19.86 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.86 19.86 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92Z" />
                </svg>
              </div>
              <div className="channel-title">Call Agent Now</div>
              <div className="channel-sub">
                {isMulti ? (
                  <>
                    <strong>{callCapableCount}</strong>{" "}
                    {callCapableCount === 1 ? "manufacturer" : "manufacturers"} will be called
                    {callDraftCount > 0 && (
                      <> · <strong>{callDraftCount}</strong> will become drafts (no phone on file)</>
                    )}.
                  </>
                ) : phoneTarget ? (
                  <>
                    Voice agent will dial <strong>{phoneTarget}</strong> and ask
                    the question on your behalf.
                  </>
                ) : (
                  "No phone number on file for this manufacturer."
                )}
              </div>
              <ul className="channel-meta">
                {isMulti ? (
                  <li>
                    <span>Callable</span> {callCapableCount} of {manufacturers.length} have a phone
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
                        <em className="warn">Outside business hours</em>
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
                    ? `Outside ${m?.manufacturer ?? "manufacturer"} business hours (${m?.mi_phone_hours ?? "unknown"}). Wait until in-hours to call.`
                    : undefined
                }
                onClick={handleCall}
              >
                {busy === "call" ? "Dialing…" : "Call Agent Now"}
              </button>
            </div>

            {/* Web Form card */}
            {webFormCapableCount > 0 && (
              <div className="channel-card">
                <div className="channel-icon channel-icon-test">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                    <path d="M9 15h6" />
                    <path d="M9 11h6" />
                  </svg>
                </div>
                <div className="channel-title">{webFormLabel}</div>
                <div className="channel-sub">
                  {isMulti ? (
                    <>
                      <strong>{webFormCapableCount}</strong>{" "}
                      {webFormCapableCount === 1 ? "manufacturer has" : "manufacturers have"} a
                      web form available.
                    </>
                  ) : (
                    "Open this manufacturer's medical information request form to submit this inquiry."
                  )}
                </div>
                <button
                  className="btn btn-primary"
                  type="button"
                  disabled={busy !== null}
                  onClick={handleOpenWebForm}
                >
                  {webFormLabel}
                </button>
                {isMulti && webFormCapableCount > 1 && (
                  <ul className="channel-meta channel-webform-list">
                    <li className="cell-muted">
                      Your browser may block opening more than one tab at
                      once — open any that didn't open individually:
                    </li>
                    {webFormManufacturers.map((wm) => (
                      <li key={wm.id}>
                        <a
                          href={wm.mi_web_form_url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {wm.manufacturer}
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          <div className="channel-foot">
            Not sure? You can also save it as a draft and decide later from the
            inquiry detail page.
          </div>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy !== null}>
            Cancel
          </button>
          <button type="button" className="btn btn-ghost" onClick={handleSaveDraft} disabled={busy !== null}>
            {busy === "draft" ? "Saving…" : "Save as Draft"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChannelChooser;
