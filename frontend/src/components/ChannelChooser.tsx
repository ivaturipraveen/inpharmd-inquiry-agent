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
  /** Called with the full E.164 number. Should call testCallPreview — no inquiry is created. */
  onTestCall: (phone: string) => Promise<void>;
  /** Called when user explicitly clicks "Save as Draft". Creates the inquiry. */
  onSaveDraft: () => Promise<void>;
  /** Called when user dismisses via ×, Escape, or backdrop. Nothing is created. */
  onClose: () => void;
}

const COUNTRY_CODES = [
  { code: "+1", label: "+1 US / CA" },
  { code: "+91", label: "+91 IN" },
  { code: "+44", label: "+44 UK" },
  { code: "+61", label: "+61 AU" },
  { code: "+49", label: "+49 DE" },
];

const digitsOnly = (s: string) => s.replace(/\D+/g, "");

const ChannelChooser: FC<Props> = ({
  manufacturers,
  fallbackHours,
  fallbackHoursVaries = false,
  inquiryLabel,
  onSendEmail,
  onCallAgent,
  onTestCall,
  onSaveDraft,
  onClose,
}) => {
  const [busy, setBusy] = useState<"email" | "call" | "draft" | "test" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [countryCode, setCountryCode] = useState("+1");
  const [testLocal, setTestLocal] = useState("");
  const [testDialedTo, setTestDialedTo] = useState<string | null>(null);

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

  const callDisabled = isMulti
    ? callCapableCount === 0 || busy !== null
    : !phoneTarget || busy !== null || outOfHours;

  const testDigits = digitsOnly(testLocal);
  const fullTestNumber = `${countryCode}${testDigits}`;
  const testValid =
    testDigits.length >= 7 && fullTestNumber.replace("+", "").length <= 15;

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);

  useEffect(() => {
    if (!testDialedTo) return;
    const t = setTimeout(() => setTestDialedTo(null), 6000);
    return () => clearTimeout(t);
  }, [testDialedTo]);

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

  const handleTestCall = async () => {
    if (!testValid) {
      setError("Enter a valid phone number (at least 7 digits).");
      return;
    }
    setBusy("test");
    setError(null);
    setTestDialedTo(null);
    try {
      await onTestCall(fullTestNumber);
      setTestDialedTo(fullTestNumber);
    } catch (e: any) {
      setError(e?.message ?? "Failed to place test call.");
    } finally {
      setBusy(null);
    }
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
          {testDialedTo && (
            <div className="success-banner">
              ✓ Test call dialing <strong>{testDialedTo}</strong> — your phone
              should ring shortly. You can now choose Send Email or Call Agent
              Now to actually contact the manufacturer.
            </div>
          )}

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
                    ? `Outside ${m?.manufacturer ?? "manufacturer"} business hours (${m?.mi_phone_hours ?? "unknown"}). Use Test Call to verify the agent, or wait until in-hours.`
                    : undefined
                }
                onClick={handleCall}
              >
                {busy === "call" ? "Dialing…" : "Call Agent Now"}
              </button>
            </div>

            {/* Test Call card */}
            <div className="channel-card channel-card-test">
              <div className="channel-icon channel-icon-test">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2v4" />
                  <path d="M12 18v4" />
                  <path d="M4.93 4.93l2.83 2.83" />
                  <path d="M16.24 16.24l2.83 2.83" />
                  <path d="M2 12h4" />
                  <path d="M18 12h4" />
                  <path d="M4.93 19.07l2.83-2.83" />
                  <path d="M16.24 7.76l2.83-2.83" />
                </svg>
              </div>
              <div className="channel-title">Test Call</div>
              <div className="channel-sub">
                Dial <strong>your own number</strong> with this inquiry's
                question and manufacturer context. Hear exactly how the agent
                would speak to a real MI desk — no status changes, no
                business-hours check.
              </div>
              <div className="phone-input-row">
                <select
                  className="phone-cc-select"
                  value={countryCode}
                  onChange={(e) => setCountryCode(e.target.value)}
                  disabled={busy !== null}
                  aria-label="Country code"
                >
                  {COUNTRY_CODES.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.label}
                    </option>
                  ))}
                </select>
                <input
                  type="tel"
                  inputMode="numeric"
                  className="channel-test-input"
                  placeholder="phone number"
                  value={testLocal}
                  onChange={(e) => setTestLocal(e.target.value)}
                  disabled={busy !== null}
                />
              </div>
              <div className="channel-test-hint">
                Dialing: <strong>{testValid ? fullTestNumber : "—"}</strong>
              </div>
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy !== null || !testValid}
                onClick={handleTestCall}
              >
                {busy === "test" ? "Dialing…" : "Call My Number"}
              </button>
            </div>
          </div>

          <div className="channel-foot">
            Not sure? You can also save it as a draft and decide later from the
            inquiry detail page.
          </div>
        </div>

        <div className="modal-footer">
          {testDialedTo ? (
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy !== null}>
              Done
            </button>
          ) : (
            <>
              <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy !== null}>
                Cancel
              </button>
              <button type="button" className="btn btn-ghost" onClick={handleSaveDraft} disabled={busy !== null}>
                {busy === "draft" ? "Saving…" : "Save as Draft"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default ChannelChooser;
