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
import { DEFAULT_FALLBACK_HOURS, FALLBACK_PRESETS } from "../utils/fallback";

interface Props {
  manufacturers: ManufacturerContact[];
  defaultManufacturerId?: number;
  defaultSubject?: string;
  defaultQuestion?: string;
  // Pre-fills Team Name when forwarded from an InpharmD MUE inquiry
  // (inquiry_submitter_details.team_name). Still editable; blank/omitted for
  // a manual inquiry with no InpharmD source.
  defaultTeamName?: string;
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

const MAX_RESULTS = 50;

const InquiryForm: FC<Props> = ({
  manufacturers,
  defaultManufacturerId,
  defaultSubject,
  defaultQuestion,
  defaultTeamName,
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
  const [teamName, setTeamName] = useState(defaultTeamName ?? "");
  const [requesterName, setRequesterName] = useState("Leah");
  const [requesterEmail, setRequesterEmail] = useState("druginfo@inpharmd.com");
  // Single source of truth for every selected manufacturer's own Drug Name +
  // fallback time — used for both the 1-manufacturer and multi-manufacturer
  // cases, so there's no separate structure to keep in sync.
  const [targetData, setTargetData] = useState<
    Record<number, { medicationName: string; fallbackHours: number }>
  >({});
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

  // Keep targetData in lockstep with manufacturerIds: seed a default entry
  // (empty Drug Name, 24h fallback — never blank) for every newly added id,
  // drop entries for removed ids, keep existing entries untouched otherwise.
  useEffect(() => {
    setTargetData(prev => {
      const next: typeof prev = {};
      for (const id of manufacturerIds) {
        next[id] = prev[id] ?? { medicationName: "", fallbackHours: DEFAULT_FALLBACK_HOURS };
      }
      return next;
    });
  }, [manufacturerIds]);

  const updateTarget = (
    id: number,
    patch: Partial<{ medicationName: string; fallbackHours: number }>
  ) => {
    setTargetData(prev => ({
      ...prev,
      [id]: {
        ...(prev[id] ?? { medicationName: "", fallbackHours: DEFAULT_FALLBACK_HOURS }),
        ...patch,
      },
    }));
  };

  const selectedMfrs = useMemo(
    () => manufacturerIds
      .map(id => manufacturers.find(m => m.id === id))
      .filter((m): m is ManufacturerContact => m != null),
    [manufacturers, manufacturerIds]
  );

  // A Drug Name is required for every selected manufacturer before the
  // inquiry can be created — used both to disable the submit button and to
  // validate on submit (a disabled submit button alone doesn't stop implicit
  // form submission via Enter).
  const missingDrugNameFor = useMemo(
    () => selectedMfrs.filter(m => !(targetData[m.id]?.medicationName ?? "").trim()),
    [selectedMfrs, targetData]
  );

  // Per-manufacturer fallback buckets — used to show independent status for
  // each selected manufacturer regardless of how many are selected.
  const fallbackEligible = useMemo(
    () => selectedMfrs.filter(m => m.fallback_call_enabled && !!m.mi_phone),
    [selectedMfrs]
  );
  const fallbackNoCallMfrs = useMemo(
    () => selectedMfrs.filter(m => !m.fallback_call_enabled),
    [selectedMfrs]
  );
  const fallbackNoPhoneMfrs = useMemo(
    () => selectedMfrs.filter(m => m.fallback_call_enabled && !m.mi_phone),
    [selectedMfrs]
  );

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

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);

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
    if (missingDrugNameFor.length > 0) {
      setError(
        missingDrugNameFor.length === 1
          ? `Enter a Drug Name for ${missingDrugNameFor[0].manufacturer}.`
          : `Enter a Drug Name for every manufacturer (missing: ${missingDrugNameFor.map(m => m.manufacturer).join(", ")}).`
      );
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
        targets: manufacturerIds.map(id => ({
          manufacturer_id: id,
          medication_name: (targetData[id]?.medicationName ?? "").trim() || null,
          fallback_after_hours: targetData[id]?.fallbackHours ?? DEFAULT_FALLBACK_HOURS,
        })),
        subject: subject.trim(),
        question: question.trim(),
        requester_name: requesterName.trim() || null,
        requester_email: requesterEmail.trim() || null,
        team_name: teamName.trim() || null,
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

              {selectedMfrs.length === 1 && (
                <div className="field full">
                  <label>
                    Drug Name<span className="req">*</span>
                  </label>
                  <input
                    type="text"
                    value={targetData[selectedMfrs[0].id]?.medicationName ?? ""}
                    onChange={(e) => updateTarget(selectedMfrs[0].id, { medicationName: e.target.value })}
                    placeholder="e.g. Drug X 500mg tablets"
                    required
                  />
                </div>
              )}

              {selectedMfrs.length > 1 && (
                <div className="field full">
                  <label>
                    Per-manufacturer details<span className="req">*</span>
                  </label>
                  <div className="mfr-target-rows">
                    <div className="mfr-target-header">
                      <span>Manufacturer</span>
                      <span>Drug Name (required)</span>
                      <span>Fallback after</span>
                    </div>
                    {selectedMfrs.map((m) => {
                      const eligible = m.fallback_call_enabled && !!m.mi_phone;
                      const data = targetData[m.id] ?? {
                        medicationName: "",
                        fallbackHours: DEFAULT_FALLBACK_HOURS,
                      };
                      return (
                        <div key={m.id} className="mfr-target-row">
                          <span className="mfr-target-row-name" title={m.manufacturer}>{m.manufacturer}</span>
                          <input
                            type="text"
                            className="mfr-target-drug-input"
                            value={data.medicationName}
                            onChange={(e) => updateTarget(m.id, { medicationName: e.target.value })}
                            placeholder="Drug name (required)"
                          />
                          {eligible ? (
                            <select
                              value={data.fallbackHours}
                              onChange={(e) =>
                                updateTarget(m.id, { fallbackHours: Number(e.target.value) })
                              }
                              className="filter-select mfr-target-fallback-select"
                            >
                              {FALLBACK_PRESETS.map((p) => (
                                <option key={p.hours} value={p.hours}>{p.label}</option>
                              ))}
                            </select>
                          ) : (
                            <span
                              className="cell-muted"
                              title={
                                !m.fallback_call_enabled
                                  ? "Fallback calling disabled"
                                  : "No MI phone on file"
                              }
                            >
                              Disabled
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="field full">
                <label>
                  Subject <span className="label-hint">system-generated</span>
                </label>
                <input
                  type="text"
                  value={subject}
                  readOnly
                  title="The subject is generated automatically from the inquiry ID and can't be edited."
                  maxLength={INQUIRY_SUBJECT_MAX_LENGTH}
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
                <label>
                  Team Name <span className="label-hint">optional</span>
                </label>
                <input
                  type="text"
                  value={teamName}
                  onChange={(e) => setTeamName(e.target.value)}
                  placeholder="e.g. MedStar Health — shown in the outbound email"
                />
              </div>

              {selectedMfrs.length === 1 && (() => {
                const soleId = selectedMfrs[0].id;
                const soleFallbackHours = targetData[soleId]?.fallbackHours ?? DEFAULT_FALLBACK_HOURS;
                const setSoleFallbackHours = (hours: number) => updateTarget(soleId, { fallbackHours: hours });
                return (
                  <div className="field full">
                    <label>If no email response within</label>
                    {fallbackEligible.length > 0 && (
                      <select
                        value={soleFallbackHours}
                        onChange={(e) => setSoleFallbackHours(Number(e.target.value))}
                        className="filter-select"
                      >
                        {FALLBACK_PRESETS.map((p) => (
                          <option key={p.hours} value={p.hours}>{p.label}</option>
                        ))}
                      </select>
                    )}
                    {fallbackEligible.length === 0 && (
                      <div className="cell-muted">
                        {fallbackNoCallMfrs.length > 0
                          ? "Fallback calling is disabled for this manufacturer."
                          : "Fallback unavailable — no MI phone number on file."}
                      </div>
                    )}
                    <div className="hint">
                      {fallbackEligible.length > 0
                        ? "After this window passes without an email reply, eligible manufacturers become eligible for an automated voice-agent fallback call."
                        : "No selected manufacturers will receive an automated fallback call if the email goes unanswered."}
                    </div>
                  </div>
                );
              })()}
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
        <button
          type="submit"
          className="btn btn-primary"
          disabled={submitting || manufacturerIds.length === 0 || missingDrugNameFor.length > 0}
          title={missingDrugNameFor.length > 0 ? "Enter a Drug Name for every selected manufacturer" : undefined}
        >
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
