import { FC, FormEvent, useEffect, useMemo, useState } from "react";
import type { InquiryInput, ManufacturerContact } from "../types";

interface Props {
  manufacturers: ManufacturerContact[];
  defaultManufacturerId?: number;
  onClose: () => void;
  onSubmit: (data: InquiryInput) => Promise<void>;
}

const FALLBACK_PRESETS = [
  { hours: 12, label: "12 hours" },
  { hours: 24, label: "24 hours" },
  { hours: 48, label: "48 hours" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
];

const InquiryForm: FC<Props> = ({
  manufacturers,
  defaultManufacturerId,
  onClose,
  onSubmit,
}) => {
  const [manufacturerId, setManufacturerId] = useState<number | "">(
    defaultManufacturerId ?? ""
  );
  const [subject, setSubject] = useState("");
  const [question, setQuestion] = useState("");
  const [requesterName, setRequesterName] = useState("");
  const [requesterEmail, setRequesterEmail] = useState("");
  const [fallbackHours, setFallbackHours] = useState(24);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (defaultManufacturerId) setManufacturerId(defaultManufacturerId);
  }, [defaultManufacturerId]);

  const selected = useMemo(
    () => manufacturers.find((m) => m.id === manufacturerId),
    [manufacturers, manufacturerId]
  );

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!manufacturerId) {
      setError("Pick a manufacturer.");
      return;
    }
    if (!subject.trim()) {
      setError("Subject is required.");
      return;
    }
    if (!question.trim()) {
      setError("Question is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({
        manufacturer_id: Number(manufacturerId),
        subject: subject.trim(),
        question: question.trim(),
        requester_name: requesterName.trim() || null,
        requester_email: requesterEmail.trim() || null,
        fallback_after_hours: fallbackHours,
      });
    } catch (err: any) {
      setError(err?.message ?? "Failed to save inquiry.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <form onSubmit={handleSubmit}>
          <div className="modal-header">
            <h2>New Inquiry</h2>
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

            <div className="form-grid">
              <div className="field full">
                <label>
                  Manufacturer<span className="req">*</span>
                </label>
                <select
                  value={manufacturerId}
                  onChange={(e) =>
                    setManufacturerId(
                      e.target.value ? Number(e.target.value) : ""
                    )
                  }
                  required
                >
                  <option value="">— Select a manufacturer —</option>
                  {manufacturers.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.manufacturer}
                    </option>
                  ))}
                </select>
                {selected && (
                  <div className="hint-row">
                    {selected.official_mi_email && (
                      <span>📧 {selected.official_mi_email}</span>
                    )}
                    {selected.mi_phone && <span>📞 {selected.mi_phone}</span>}
                    {selected.typical_response_sla && (
                      <span>⏱ SLA: {selected.typical_response_sla}</span>
                    )}
                  </div>
                )}
              </div>

              <div className="field full">
                <label>
                  Subject<span className="req">*</span>
                </label>
                <input
                  type="text"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  placeholder="e.g. Stability data for Drug X after temperature excursion"
                  required
                />
              </div>

              <div className="field full">
                <label>
                  Question / Details<span className="req">*</span>
                </label>
                <textarea
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  rows={5}
                  placeholder="Describe what you need from the manufacturer…"
                  required
                />
              </div>

              <div className="field">
                <label>Requester Name</label>
                <input
                  type="text"
                  value={requesterName}
                  onChange={(e) => setRequesterName(e.target.value)}
                />
              </div>
              <div className="field">
                <label>Requester Email</label>
                <input
                  type="email"
                  value={requesterEmail}
                  onChange={(e) => setRequesterEmail(e.target.value)}
                  placeholder="reply-to address"
                />
              </div>

              <div className="field full">
                <label>If no email response within</label>
                <div className="preset-row">
                  {FALLBACK_PRESETS.map((p) => (
                    <button
                      type="button"
                      key={p.hours}
                      className={`preset-chip ${
                        fallbackHours === p.hours ? "preset-chip-active" : ""
                      }`}
                      onClick={() => setFallbackHours(p.hours)}
                    >
                      {p.label}
                    </button>
                  ))}
                  <input
                    type="number"
                    min={1}
                    max={720}
                    value={fallbackHours}
                    onChange={(e) =>
                      setFallbackHours(Math.max(1, Number(e.target.value)))
                    }
                    className="preset-num"
                  />
                  <span className="preset-unit">hours</span>
                </div>
                <div className="hint">
                  After this window passes without an email reply, the inquiry
                  becomes eligible for an automated voice-agent fallback call.
                </div>
              </div>
            </div>
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? "Saving…" : "Create Inquiry"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default InquiryForm;
