import { FC, useEffect, useState } from "react";
import { api } from "../api";
import type { Inquiry } from "../types";

interface Props {
  inquiry: Inquiry;
  onSendEmail: () => Promise<void>;
  onCallTriggered: () => void;
  onClose: () => void;
}

interface HoursInfo {
  known: boolean;
  in_hours?: boolean;
  hours_text?: string | null;
  phone?: string | null;
}

const ChannelChooser: FC<Props> = ({
  inquiry,
  onSendEmail,
  onCallTriggered,
  onClose,
}) => {
  const [hours, setHours] = useState<HoursInfo | null>(null);
  const [busy, setBusy] = useState<"email" | "call" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.inquiries.businessHours(inquiry.id).then(setHours).catch(() => {});
  }, [inquiry.id]);

  const m = inquiry.manufacturer;
  const emailTarget = m?.official_mi_email || m?.team_verified_email;
  const phoneTarget = hours?.phone || m?.mi_phone;

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

  const handleCall = async (force = false) => {
    setBusy("call");
    setError(null);
    try {
      await api.inquiries.triggerCall(inquiry.id, force);
      onCallTriggered();
    } catch (e: any) {
      const msg = e?.message ?? "Failed to place call.";
      // ElevenLabs config missing
      if (msg.includes("503")) {
        setError(
          "ElevenLabs is not configured yet. Add ELEVENLABS_API_KEY / " +
            "ELEVENLABS_AGENT_ID / ELEVENLABS_AGENT_PHONE_NUMBER_ID to backend/.env and restart."
        );
      } else if (msg.includes("out_of_hours") && !force) {
        if (
          confirm(
            `${m?.manufacturer} is outside business hours (${hours?.hours_text ?? "unknown"}). Call anyway?`
          )
        ) {
          return handleCall(true);
        }
      } else {
        setError(msg);
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
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
                disabled={!phoneTarget || busy !== null}
                onClick={() => handleCall(false)}
              >
                {busy === "call" ? "Dialing…" : "Call Agent Now"}
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
            Save as Draft
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChannelChooser;
