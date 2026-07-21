import {
  FC,
  FormEvent,
  KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ClientTools } from "@elevenlabs/react";
import type { InquiryInput, ManufacturerContact } from "../types";
import { INQUIRY_SUBJECT_MAX_LENGTH } from "../types";
import VoiceFillButton from "./VoiceFillButton";

interface Props {
  manufacturers: ManufacturerContact[];
  defaultManufacturerId?: number;
  defaultSubject?: string;
  defaultQuestion?: string;
  // Optional banner shown at the top of the form — used when this form is
  // launched from a context like "Contact manufacturer about InpharmD #1234"
  // so the user has a clear visual cue about what they're forwarding.
  contextNote?: string;
  // "modal" = floating dialog over a backdrop (default, used by Outreach tab).
  // "page"  = inline full-page form (used by Contact Manufacturer page).
  variant?: "modal" | "page";
  // Optional override for the form heading.
  title?: string;
  // Optional submit button label (defaults to "Create Inquiry").
  submitLabel?: string;
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

const PRESET_HOURS = FALLBACK_PRESETS.map((p) => p.hours);

const MAX_RESULTS = 50;

const InquiryForm: FC<Props> = ({
  manufacturers,
  defaultManufacturerId,
  defaultSubject,
  defaultQuestion,
  contextNote,
  variant = "modal",
  title = "New Inquiry",
  submitLabel,
  onClose,
  onSubmit,
}) => {
  const [manufacturerId, setManufacturerId] = useState<number | "">(
    defaultManufacturerId ?? ""
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
    if (defaultManufacturerId) setManufacturerId(defaultManufacturerId);
  }, [defaultManufacturerId]);

  const selected = useMemo(
    () => manufacturers.find((m) => m.id === manufacturerId),
    [manufacturers, manufacturerId]
  );

  // Filter manufacturers by query (case-insensitive substring on name + parent).
  const filtered = useMemo(() => {
    const q = mfrQuery.trim().toLowerCase();
    if (!q) return manufacturers.slice(0, MAX_RESULTS);
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

  const pickManufacturer = (m: ManufacturerContact) => {
    setManufacturerId(m.id);
    setMfrQuery("");
    setMfrOpen(false);
  };

  const clearManufacturer = () => {
    setManufacturerId("");
    setMfrQuery("");
    setTimeout(() => {
      mfrInputRef.current?.focus();
      setMfrOpen(true);
    }, 0);
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
        pickManufacturer(filtered[activeIndex]);
      }
    } else if (e.key === "Escape") {
      setMfrOpen(false);
    }
  };

  // Client tools the voice agent can call to fill this form.
  const voiceTools: ClientTools = useMemo(() => {
    const norm = (s: string) =>
      s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

    const findManufacturer = (raw: string) => {
      const q = norm(raw);
      if (!q) return { exact: null, candidates: [] as ManufacturerContact[] };
      // Exact name match wins
      const exact = manufacturers.find((m) => norm(m.manufacturer) === q);
      if (exact) return { exact, candidates: [exact] };
      // Then startsWith / contains on name or parent
      const ranked = manufacturers
        .map((m) => {
          const name = norm(m.manufacturer);
          const parent = norm(m.parent_owner ?? "");
          let score = 0;
          if (name.startsWith(q)) score = 100;
          else if (name.includes(q)) score = 70;
          else if (parent.startsWith(q)) score = 50;
          else if (parent.includes(q)) score = 30;
          return { m, score };
        })
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 5)
        .map((x) => x.m);
      return { exact: ranked[0] && ranked.length === 1 ? ranked[0] : null, candidates: ranked };
    };

    return {
      pick_manufacturer: async (params) => {
        const name = String(params?.name ?? "").trim();
        if (!name) return "No manufacturer name given.";
        const { exact, candidates } = findManufacturer(name);
        if (exact) {
          setManufacturerId(exact.id);
          return `Selected ${exact.manufacturer}.`;
        }
        if (candidates.length === 0)
          return `No manufacturer matches "${name}". Try a different spelling.`;
        if (candidates.length === 1) {
          setManufacturerId(candidates[0].id);
          return `Selected ${candidates[0].manufacturer}.`;
        }
        return `Multiple matches for "${name}": ${candidates
          .map((c) => c.manufacturer)
          .join(", ")}. Ask the user which one.`;
      },
      set_field: async (params) => {
        const field = String(params?.field ?? "").trim();
        const value = params?.value;
        const str = value == null ? "" : String(value);
        switch (field) {
          case "subject":
            setSubject(str);
            return `Subject set.`;
          case "question":
            setQuestion(str);
            return `Question set.`;
          case "requester_name":
            setRequesterName(str);
            return `Requester name set.`;
          case "requester_email":
            setRequesterEmail(str);
            return `Requester email set.`;
          case "fallback_hours": {
            const n = Number(value);
            if (!Number.isFinite(n) || n < 1)
              return `Invalid fallback_hours value "${str}".`;
            setFallbackHours(n);
            setCustomMode(!PRESET_HOURS.includes(n));
            return `Fallback set to ${n} hours.`;
          }
          default:
            return `Unknown field "${field}". Valid fields: subject, question, requester_name, requester_email, fallback_hours.`;
        }
      },
      submit_form: async () => {
        if (!manufacturerId) return "Cannot submit — pick a manufacturer first.";
        if (!subject.trim()) return "Cannot submit — subject is required.";
        if (!question.trim()) return "Cannot submit — question is required.";
        formRef.current?.requestSubmit();
        return "Submitting the inquiry now.";
      },
    };
  }, [manufacturers, manufacturerId, subject, question]);

  const voiceVars = useMemo(
    () => ({
      form_type: "inquiry",
      manufacturer_count: manufacturers.length,
      current_subject: subject || "(empty)",
      current_question_length: question.length,
      requester_name: requesterName,
      requester_email: requesterEmail,
      fallback_hours: fallbackHours,
    }),
    [manufacturers.length, subject, question, requesterName, requesterEmail, fallbackHours]
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

  const inner = (
    <form ref={formRef} onSubmit={handleSubmit}>
      <div className={variant === "page" ? "page-form-header" : "modal-header"}>
        <h2>{title}</h2>
        <div className="modal-header-actions">
          <VoiceFillButton
            clientTools={voiceTools}
            dynamicVariables={voiceVars}
          />
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

                {selected ? (
                  <div className="mfr-selected">
                    <div className="mfr-selected-main">
                      <span className="mfr-selected-check">✓</span>
                      <div>
                        <div className="mfr-selected-name">
                          {selected.manufacturer}
                        </div>
                        {selected.parent_owner && (
                          <div className="mfr-selected-parent">
                            {selected.parent_owner}
                          </div>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="mfr-clear"
                      onClick={clearManufacturer}
                      aria-label="Change manufacturer"
                      title="Change manufacturer"
                    >
                      ×
                    </button>
                  </div>
                ) : (
                  <div className="mfr-combo" ref={mfrWrapRef}>
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
                        placeholder={`Search ${manufacturers.length} manufacturers…`}
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
                                className={`mfr-combo-item ${
                                  idx === activeIndex
                                    ? "mfr-combo-item-active"
                                    : ""
                                }`}
                                onMouseEnter={() => setActiveIndex(idx)}
                                onClick={() => pickManufacturer(m)}
                              >
                                <span className="mfr-combo-name">
                                  {m.manufacturer}
                                </span>
                                {m.parent_owner && (
                                  <span className="mfr-combo-parent">
                                    {m.parent_owner}
                                  </span>
                                )}
                              </button>
                            ))}
                            {filtered.length === MAX_RESULTS && (
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
                )}

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
                <div className="hint">
                  After this window passes without an email reply, the inquiry
                  becomes eligible for an automated voice-agent fallback call.
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
