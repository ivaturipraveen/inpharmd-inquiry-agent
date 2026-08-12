import {
  FC,
  FormEvent,
  KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { InquiryFormData, ManufacturerContact } from "../types";
import { INQUIRY_SUBJECT_MAX_LENGTH } from "../types";

interface Props {
  manufacturers: ManufacturerContact[];
  defaultManufacturerId?: number;
  defaultSubject?: string;
  defaultQuestion?: string;
  // "modal" = floating dialog over a backdrop (default, used by Outreach tab).
  // "page"  = inline full-page form (used by Contact Manufacturer page).
  variant?: "modal" | "page";
  // Optional override for the form heading.
  title?: string;
  // Optional submit button label (defaults to "Create Inquiry").
  submitLabel?: string;
  onClose: () => void;
  onSubmit: (data: InquiryFormData) => Promise<void>;
}

const FALLBACK_PRESETS = [
  { hours: 0, label: "5 min (testing)" },
  { hours: 12, label: "12 hours" },
  { hours: 24, label: "24 hours" },
  { hours: 48, label: "48 hours" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "7 days" },
];

const PRESET_HOURS = FALLBACK_PRESETS.map((p) => p.hours);

const MAX_RESULTS = 50;

const InquiryForm: FC<Props> = ({
  manufacturers,
  defaultManufacturerId,
  defaultSubject,
  defaultQuestion,
  variant = "modal",
  title = "New Inquiry",
  submitLabel,
  onClose,
  onSubmit,
}) => {
  const [manufacturerIds, setManufacturerIds] = useState<number[]>(
    defaultManufacturerId != null ? [defaultManufacturerId] : []
  );
  const [mfrQuery, setMfrQuery] = useState("");
  const [mfrOpen, setMfrOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const mfrWrapRef = useRef<HTMLDivElement | null>(null);
  const mfrInputRef = useRef<HTMLInputElement | null>(null);

  const [subject, setSubject] = useState(defaultSubject ?? "");
  const [question, setQuestion] = useState(defaultQuestion ?? "");
  const [requesterName, setRequesterName] = useState("Leah");
  const [requesterEmail, setRequesterEmail] = useState("druginfo@inpharmd.com");
  const [fallbackHours, setFallbackHours] = useState(24);
  const [customMode, setCustomMode] = useState(false);
  const [customValue, setCustomValue] = useState(24);
  const [customUnit, setCustomUnit] = useState<"hours" | "days">("hours");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    if (defaultManufacturerId != null) {
      setManufacturerIds(prev =>
        prev.includes(defaultManufacturerId) ? prev : [defaultManufacturerId]
      );
    }
  }, [defaultManufacturerId]);

  const selectedMfrs = useMemo(
    () => manufacturerIds
      .map(id => manufacturers.find(m => m.id === id))
      .filter((m): m is ManufacturerContact => m != null),
    [manufacturers, manufacturerIds]
  );

  // Only assert "disabled" when exactly one manufacturer is selected and its
  // flag is explicitly false — with multiple selected manufacturers, whether
  // fallback applies can differ per manufacturer, so the shared picker is
  // left as-is rather than guessing which one the state should reflect.
  const fallbackDisabled =
    selectedMfrs.length === 1 && selectedMfrs[0].fallback_call_enabled === false;

  // Filter manufacturers by query (case-insensitive substring on name + parent).
  const filtered = useMemo(() => {
    const q = mfrQuery.trim().toLowerCase();
    if (!q) return manufacturers;
    return manufacturers
      .filter((m) => {
        const hay = `${m.manufacturer} ${m.parent_owner ?? ""}`.toLowerCase();
        return hay.includes(q);
      })
      .slice(0, MAX_RESULTS);
  }, [manufacturers, mfrQuery]);

  // Close the dropdown when clicking outside the combobox.
  useEffect(() => {
    if (!mfrOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!mfrWrapRef.current) return;
      if (!mfrWrapRef.current.contains(e.target as Node)) {
        setMfrOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [mfrOpen]);

  // Reset the highlighted row whenever the filtered list changes.
  useEffect(() => {
    setActiveIndex(0);
  }, [mfrQuery, mfrOpen]);

  const toggleManufacturer = (m: ManufacturerContact) => {
    setManufacturerIds(prev =>
      prev.includes(m.id) ? prev.filter(id => id !== m.id) : [...prev, m.id]
    );
    setMfrQuery("");
  };

  const removeManufacturer = (id: number) => {
    setManufacturerIds(prev => prev.filter(x => x !== id));
    setTimeout(() => mfrInputRef.current?.focus(), 0);
  };

  const handleMfrKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setMfrOpen(true);
      setActiveIndex((i) => Math.min(i + 1, Math.max(0, filtered.length - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      if (mfrOpen && filtered[activeIndex]) {
        e.preventDefault();
        toggleManufacturer(filtered[activeIndex]);
      }
    } else if (e.key === "Escape") {
      setMfrOpen(false);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (manufacturerIds.length === 0) {
      setError("Pick at least one manufacturer.");
      return;
    }
    if (!subject.trim()) {
      setError("Subject is required.");
      return;
    }
    if (subject.trim().length > INQUIRY_SUBJECT_MAX_LENGTH) {
      setError(`Subject must be ${INQUIRY_SUBJECT_MAX_LENGTH} characters or fewer.`);
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
        manufacturer_ids: manufacturerIds,
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

  const inner = (
    <form ref={formRef} onSubmit={handleSubmit}>
      <div className={variant === "page" ? "page-form-header" : "modal-header"}>
        <h2>{title}</h2>
        <div className="modal-header-actions">
          {variant === "modal" && (
            <button
              type="button"
              className="modal-close"
              onClick={onClose}
              aria-label="Close"
            >
              ×
            </button>
          )}
        </div>
      </div>

      <div className={variant === "page" ? "page-form-body" : "modal-body"}>
            {error && <div className="error-banner">{error}</div>}

            <div className="form-grid">
              <div className="field full">
                <label>
                  Manufacturer<span className="req">*</span>
                </label>

                <div className="mfr-combo" ref={mfrWrapRef}>
                  {selectedMfrs.length > 0 && (
                    <div className="mfr-pills">
                      {selectedMfrs.map(m => (
                        <span key={m.id} className="mfr-pill">
                          <span className="mfr-pill-name">{m.manufacturer}</span>
                          <button
                            type="button"
                            className="mfr-pill-remove"
                            onClick={() => removeManufacturer(m.id)}
                            aria-label={`Remove ${m.manufacturer}`}
                          >×</button>
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="mfr-combo-input-wrap">
                    <svg
                      className="mfr-combo-icon"
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
                      ref={mfrInputRef}
                      type="text"
                      className="mfr-combo-input"
                      placeholder={
                        selectedMfrs.length > 0
                          ? `Add more (${selectedMfrs.length} selected)…`
                          : `Search ${manufacturers.length} manufacturers…`
                      }
                      value={mfrQuery}
                      onChange={(e) => {
                        setMfrQuery(e.target.value);
                        setMfrOpen(true);
                      }}
                      onFocus={() => setMfrOpen(true)}
                      onKeyDown={handleMfrKeyDown}
                      autoComplete="off"
                    />
                  </div>
                  {mfrOpen && (
                    <div className="mfr-combo-menu">
                      {filtered.length === 0 ? (
                        <div className="mfr-combo-empty">
                          No manufacturers match "{mfrQuery}".
                        </div>
                      ) : (
                        <>
                          {filtered.map((m, idx) => (
                            <button
                              type="button"
                              key={m.id}
                              className={`mfr-combo-item ${idx === activeIndex ? "mfr-combo-item-active" : ""}`}
                              onMouseEnter={() => setActiveIndex(idx)}
                              onClick={() => toggleManufacturer(m)}
                            >
                              <div className="mfr-combo-item-inner">
                                <span className="mfr-combo-item-check">
                                  {manufacturerIds.includes(m.id) ? "✓" : ""}
                                </span>
                                <div>
                                  <div className="mfr-combo-name">{m.manufacturer}</div>
                                  {m.parent_owner && (
                                    <div className="mfr-combo-parent">{m.parent_owner}</div>
                                  )}
                                </div>
                              </div>
                            </button>
                          ))}
                          {mfrQuery.trim() && filtered.length === MAX_RESULTS && (
                            <div className="mfr-combo-empty">
                              Showing first {MAX_RESULTS} matches — refine your
                              search to narrow further.
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>

                {selectedMfrs.length === 1 && (
                  <div className="hint-row">
                    {selectedMfrs[0].official_mi_email && (
                      <span>📧 {selectedMfrs[0].official_mi_email}</span>
                    )}
                    {selectedMfrs[0].mi_phone && <span>📞 {selectedMfrs[0].mi_phone}</span>}
                    {selectedMfrs[0].typical_response_sla && (
                      <span>⏱ SLA: {selectedMfrs[0].typical_response_sla}</span>
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
                  maxLength={INQUIRY_SUBJECT_MAX_LENGTH}
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
                  placeholder="e.g. A pharmacy received Drug X exposed to 30°C for 6 hours during transit. What's the stability data — can it still be dispensed? Please reference the PI section."
                  required
                />
              </div>

              <div className="field">
                <label>
                  Requester Name <span className="label-hint">optional</span>
                </label>
                <input
                  type="text"
                  value={requesterName}
                  onChange={(e) => setRequesterName(e.target.value)}
                  placeholder="Pharmacist name (for audit log)"
                />
              </div>
              <div className="field">
                <label>
                  Requester Email <span className="label-hint">optional</span>
                </label>
                <input
                  type="email"
                  value={requesterEmail}
                  onChange={(e) => setRequesterEmail(e.target.value)}
                  placeholder="Where the rep should reply / send follow-up"
                />
              </div>

              <div className="field full">
                <label>If no email response within</label>
                {fallbackDisabled ? (
                  <div className="cell-muted">
                    Disabled — {selectedMfrs[0].manufacturer} does not have fallback calling enabled.
                  </div>
                ) : (
                <>
                <div className="preset-row">
                  {FALLBACK_PRESETS.map((p) => (
                    <button
                      type="button"
                      key={p.hours}
                      className={`preset-chip ${
                        !customMode && fallbackHours === p.hours
                          ? "preset-chip-active"
                          : ""
                      }`}
                      onClick={() => {
                        setCustomMode(false);
                        setFallbackHours(p.hours);
                      }}
                    >
                      {p.label}
                    </button>
                  ))}
                  <button
                    type="button"
                    className={`preset-chip ${
                      customMode ? "preset-chip-active" : ""
                    }`}
                    onClick={() => {
                      setCustomMode(true);
                      const initial =
                        !PRESET_HOURS.includes(fallbackHours)
                          ? fallbackHours
                          : 24;
                      setCustomValue(
                        customUnit === "days" ? Math.ceil(initial / 24) : initial
                      );
                      setFallbackHours(initial);
                    }}
                  >
                    Custom…
                  </button>
                </div>
                {customMode && (
                  <div className="custom-row">
                    <input
                      type="number"
                      min={1}
                      max={customUnit === "days" ? 30 : 720}
                      value={customValue}
                      onChange={(e) => {
                        const v = Math.max(1, Number(e.target.value) || 1);
                        setCustomValue(v);
                        setFallbackHours(customUnit === "days" ? v * 24 : v);
                      }}
                      className="preset-num"
                    />
                    <div className="unit-toggle">
                      <button
                        type="button"
                        className={
                          customUnit === "hours" ? "unit-active" : ""
                        }
                        onClick={() => {
                          setCustomUnit("hours");
                          setFallbackHours(customValue);
                        }}
                      >
                        hours
                      </button>
                      <button
                        type="button"
                        className={
                          customUnit === "days" ? "unit-active" : ""
                        }
                        onClick={() => {
                          setCustomUnit("days");
                          setFallbackHours(customValue * 24);
                        }}
                      >
                        days
                      </button>
                    </div>
                  </div>
                )}
                </>
                )}
                <div className="hint">
                  {fallbackDisabled
                    ? "This manufacturer will not receive an automated fallback call if the email goes unanswered."
                    : "After this window passes without an email reply, the inquiry becomes eligible for an automated voice-agent fallback call."}
                </div>
              </div>
            </div>

        <div className="form-foot">
          <strong>What happens next?</strong> Once you click {submitLabel ?? "Create Inquiry"},
          you'll choose how to reach this manufacturer — send an email, or
          have the voice agent call them now.
        </div>
      </div>

      <div className={variant === "page" ? "page-form-footer" : "modal-footer"}>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={onClose}
          disabled={submitting}
        >
          Cancel
        </button>
        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? "Saving…" : submitLabel ?? "Create Inquiry"}
        </button>
      </div>
    </form>
  );

  if (variant === "page") {
    return <div className="page-form">{inner}</div>;
  }
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {inner}
      </div>
    </div>
  );
};

export default InquiryForm;
