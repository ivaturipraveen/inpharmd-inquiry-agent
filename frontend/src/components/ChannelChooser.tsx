import { FC, useEffect, useState } from "react";
import { api } from "../api";
import type { Inquiry } from "../types";

interface Props {
  inquiry: Inquiry;
  onSendEmail: () => Promise<void>;
  onCallTriggered: () => void;
  onTestCallTriggered?: (phone: string) => void;
  onClose: () => void;
}

interface HoursInfo {
  known: boolean;
  in_hours?: boolean;
  hours_text?: string | null;
  phone?: string | null;
}

type Busy = "email" | "call" | "test" | null;

const COUNTRY_CODES = [
  { code: "+1", label: "+1 US / CA" },
  { code: "+91", label: "+91 IN" },
  { code: "+44", label: "+44 UK" },
  { code: "+61", label: "+61 AU" },
  { code: "+49", label: "+49 DE" },
];

// Strip every non-digit. Used for both validation and final E.164 assembly.
const digitsOnly = (s: string) => s.replace(/\D+/g, "");

const ChannelChooser: FC<Props> = ({
  inquiry,
  onSendEmail,
  onCallTriggered,
  onTestCallTriggered,
  onClose,
}) => {
  const [hours, setHours] = useState<HoursInfo | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [countryCode, setCountryCode] = useState("+1");
  const [testLocal, setTestLocal] = useState("");
  const [testDialedTo, setTestDialedTo] = useState<string | null>(null);

  useEffect(() => {
    api.inquiries.businessHours(inquiry.id).then(setHours).catch(() => {});
  }, [inquiry.id]);

  const m = inquiry.manufacturer;
  const emailTarget = m?.official_mi_email || m?.team_verified_email;
  const phoneTarget = hours?.phone || m?.mi_phone;
  const callInFlight = inquiry.status === "call_pending";
  // Treat "hours known and explicitly out-of-hours" as blocking. Unknown hours
  // are still allowed — we can't prove it's wrong, and many manufacturer rows
  // have no hours text.
  const outOfHours = hours?.known === true && hours?.in_hours === false;
  const callDisabled =
    !phoneTarget || busy !== null || callInFlight || outOfHours;

  const testDigits = digitsOnly(testLocal);
  const fullTestNumber = `${countryCode}${testDigits}`;
  // E.164 max is 15 digits including country code. Local portion needs at
  // least 7 digits to be a plausible phone number.
  const testValid =
    testDigits.length >= 7 &&
    fullTestNumber.replace("+", "").length <= 15;

  const handleEmail = async () => {
    setBusy("email");
    setError(null);
    try {
      await onSendEmail();
    } catch (e: any) {
      setError(e?.message ?? "Failed to send email.");
    } finally {
      setBusy(null);
    }
  };

  const handleCall = async () => {
    if (outOfHours) return;
    setBusy("call");
    setError(null);
    try {
      await api.inquiries.triggerCall(inquiry.id, false);
      onCallTriggered();
    } catch (e: any) {
      const msg = e?.message ?? "Failed to place call.";
      if (msg.includes("503")) {
        setError(
          "ElevenLabs is not configured yet. Add ELEVENLABS_API_KEY / " +
            "ELEVENLABS_AGENT_ID / ELEVENLABS_AGENT_PHONE_NUMBER_ID to backend/.env and restart."
        );
      } else {
        setError(msg);
      }
    } finally {
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
      await api.inquiries.testCall(inquiry.id, fullTestNumber);
      setTestDialedTo(fullTestNumber);
      // Notify the parent for a toast, but DO NOT close the modal — the user
      // typically wants to test first and then send the real email / place
      // the real call from the same view.
      onTestCallTriggered?.(fullTestNumber);
    } catch (e: any) {
      setError(e?.message ?? "Failed to place test call.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="meta-text">Inquiry #{inquiry.id} created</div>
            <h2>How should we reach {m?.manufacturer ?? "the manufacturer"}?</h2>
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
            <div className={`channel-card ${!emailTarget ? "channel-disabled" : ""}`}>
              <div className="channel-icon channel-icon-email">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="5" width="18" height="14" rx="2" />
                  <path d="m3 7 9 6 9-6" />
                </svg>
              </div>
              <div className="channel-title">Send Email</div>
              <div className="channel-sub">
                {emailTarget ? (
                  <>
                    We'll send to{" "}
                    <strong>{emailTarget}</strong> and wait{" "}
                    <strong>{inquiry.fallback_after_hours}h</strong> for a reply
                    before the voice agent calls.
                  </>
                ) : (
                  "No email on file for this manufacturer."
                )}
              </div>
              <ul className="channel-meta">
                <li>
                  <span>SLA</span> {m?.typical_response_sla ?? "—"}
                </li>
                <li>
                  <span>Fallback</span> Agent call after {inquiry.fallback_after_hours}h
                </li>
              </ul>
              <button
                className="btn btn-primary"
                type="button"
                disabled={!emailTarget || busy !== null}
                onClick={handleEmail}
              >
                {busy === "email" ? "Sending…" : "Send Email"}
              </button>
            </div>

            {/* Call card */}
            <div className={`channel-card ${!phoneTarget ? "channel-disabled" : ""}`}>
              <div className="channel-icon channel-icon-call">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.86 19.86 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.86 19.86 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92Z" />
                </svg>
              </div>
              <div className="channel-title">Call Agent Now</div>
              <div className="channel-sub">
                {phoneTarget ? (
                  <>
                    Voice agent will dial <strong>{phoneTarget}</strong> and ask
                    the question on your behalf.
                  </>
                ) : (
                  "No phone number on file for this manufacturer."
                )}
              </div>
              <ul className="channel-meta">
                <li>
                  <span>Hours</span>{" "}
                  {hours?.hours_text ?? m?.typical_response_sla ?? "—"}
                </li>
                <li>
                  <span>Status</span>{" "}
                  {hours?.known === false ? (
                    <em className="warn">unknown</em>
                  ) : hours?.in_hours ? (
                    <em className="ok">In business hours now</em>
                  ) : (
                    <em className="warn">Outside business hours</em>
                  )}
                </li>
              </ul>
              <button
                className="btn btn-primary"
                type="button"
                disabled={callDisabled}
                title={
                  outOfHours
                    ? `Outside ${m?.manufacturer ?? "manufacturer"} business hours (${hours?.hours_text ?? "unknown"}). Use Test Call to verify the agent, or wait until in-hours.`
                    : undefined
                }
                onClick={handleCall}
              >
                {callInFlight
                  ? "Call in progress…"
                  : outOfHours
                  ? "Outside business hours"
                  : busy === "call"
                  ? "Dialing…"
                  : "Call Agent Now"}
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
                would speak to a real MI desk — no status changes, no business-
                hours check.
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
                Dialing: <strong>{testValid ? fullTestNumber : "—"}</strong>{" "}
                · won't affect inquiry #{inquiry.id}.
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
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            {testDialedTo ? "Done" : "Save as Draft"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChannelChooser;
