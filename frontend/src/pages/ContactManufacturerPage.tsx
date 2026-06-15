import { useCallback, useEffect, useMemo, useState } from "react";
import InquiryForm from "../components/InquiryForm";
import ChannelChooser from "../components/ChannelChooser";
import { api } from "../api";
import { isWithinBusinessHoursNow } from "../utils/businessHours";
import type {
  Inquiry,
  InquiryInput,
  ManufacturerContact,
} from "../types";

interface Attachment {
  id: number;
  file_name: string;
  doc_url: string;
}

interface ForwardContext {
  uuid: string;
  title: string;
  submitter?: string;
  type?: string;
  attachments?: Attachment[];
}

interface DetectedRow {
  row_index: number;
  raw_name: string;
  matched_id: number | null;
  matched_name: string | null;
  confidence: "exact" | "partial" | "loose" | "none";
}

interface DetectedExtraction {
  sheet_name: string;
  header_row: number;
  header_value: string;
  total: number;
  matched: number;
  excel_s3_url: string | null;
  rows: DetectedRow[];
}

const CTX_KEY = "inpharmd:contact-manufacturer:ctx";

const readQuery = (): URLSearchParams => {
  const qs = window.location.hash.split("?")[1] ?? "";
  return new URLSearchParams(qs);
};

const readContext = (): ForwardContext | null => {
  try {
    const raw = sessionStorage.getItem(CTX_KEY);
    if (raw) {
      const ctx = JSON.parse(raw) as ForwardContext;
      if (ctx?.uuid) return ctx;
    }
  } catch {
    /* ignore */
  }
  const params = readQuery();
  const uuid = params.get("uuid") ?? "";
  const title = params.get("title") ?? "";
  if (!uuid && !title) return null;
  return { uuid, title };
};

const goTo = (hash: string) => {
  window.location.hash = hash;
};

const isXlsx = (a: Attachment): boolean =>
  /\.xlsx(\?|$)/i.test(a.file_name) || /\.xlsx(\?|$)/i.test(a.doc_url);

const confidenceLabel = (c: DetectedRow["confidence"]): string => {
  switch (c) {
    case "exact": return "Exact";
    case "partial": return "Partial";
    case "loose": return "Loose";
    default: return "—";
  }
};

const confidenceTone = (c: DetectedRow["confidence"]): string => {
  switch (c) {
    case "exact": return "good";
    case "partial": return "info";
    case "loose": return "warn";
    default: return "neutral";
  }
};

type Mode = "single" | "multi";
type BulkChannel = "email" | "call" | "test_call";

const COUNTRY_CODES: { code: string; label: string }[] = [
  { code: "+1", label: "+1 US / CA" },
  { code: "+91", label: "+91 IN" },
  { code: "+44", label: "+44 UK" },
  { code: "+61", label: "+61 AU" },
  { code: "+49", label: "+49 DE" },
];

const digitsOnly = (s: string) => s.replace(/\D+/g, "");

export default function ContactManufacturerPage() {
  const [ctx] = useState<ForwardContext | null>(readContext);
  const [manufacturers, setManufacturers] = useState<ManufacturerContact[]>([]);
  const [loadingMfrs, setLoadingMfrs] = useState(true);
  const [pendingChoice, setPendingChoice] = useState<Inquiry | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Multi-dispatch state
  const [mode, setMode] = useState<Mode>("single");
  const [extraction, setExtraction] = useState<DetectedExtraction | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [selectedRows, setSelectedRows] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [subject, setSubject] = useState("");
  const [question, setQuestion] = useState("");
  const [fallbackHours, setFallbackHours] = useState(24);
  const [submitting, setSubmitting] = useState<BulkChannel | null>(null);
  // Test-call inputs
  const [testCountryCode, setTestCountryCode] = useState("+1");
  const [testLocal, setTestLocal] = useState("");
  const [bulkResult, setBulkResult] = useState<{
    created: Inquiry[];
    failed: { manufacturer_id: number; error: string }[];
    dispatch_channel?: string;
    dispatched?: number;
    test_call_inquiry_id?: number | null;
    test_call_to?: string | null;
  } | null>(null);

  useEffect(() => {
    api.manufacturers
      .list()
      .then(setManufacturers)
      .catch((e: any) => setError(e?.message ?? "Failed to load manufacturers."))
      .finally(() => setLoadingMfrs(false));
  }, []);

  useEffect(() => {
    if (ctx) sessionStorage.removeItem(CTX_KEY);
    if (ctx?.title) {
      setSubject(ctx.title);
      setQuestion(ctx.title);
    }
  }, [ctx]);

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 3500);
    return () => clearTimeout(t);
  }, [banner]);

  const xlsxAttachment = useMemo(() => {
    return (ctx?.attachments ?? []).find(isXlsx);
  }, [ctx]);

  // Quick lookup for matched-manufacturer details (email/phone/SLA).
  const mfrById = useMemo(() => {
    const m: Record<number, ManufacturerContact> = {};
    for (const x of manufacturers) m[x.id] = x;
    return m;
  }, [manufacturers]);

  const handleCancel = useCallback(() => {
    goTo("platform-inquiries");
  }, []);

  const handleSingleCreate = useCallback(
    async (data: InquiryInput) => {
      const payload: InquiryInput = ctx?.uuid
        ? { ...data, source_inquiry_uuid: ctx.uuid }
        : data;
      const created = await api.inquiries.create(payload);
      setBanner(`Inquiry #${created.id} created — choose how to reach the manufacturer.`);
      setPendingChoice(created);
    },
    [ctx],
  );

  const runExtraction = useCallback(async () => {
    if (!xlsxAttachment) return;
    setExtracting(true);
    setExtractError(null);
    try {
      const result = await api.externalInquiries.extractManufacturers(
        xlsxAttachment.doc_url,
        ctx?.uuid,
      );
      setExtraction(result);
      // Pre-select every row that has a confident match (exact + partial).
      const pre = new Set<number>();
      for (const r of result.rows) {
        if (r.matched_id && (r.confidence === "exact" || r.confidence === "partial")) {
          pre.add(r.row_index);
        }
      }
      setSelectedRows(pre);
      setMode("multi");
    } catch (e: any) {
      setExtractError(
        e?.message ?? "Could not read manufacturers from the spreadsheet.",
      );
    } finally {
      setExtracting(false);
    }
  }, [xlsxAttachment, ctx]);

  // Auto-detect on mount if the inquiry has an .xlsx attachment. No button —
  // the user lands on a populated checklist instead of an empty form.
  useEffect(() => {
    if (!xlsxAttachment || extraction || extracting) return;
    runExtraction();
  }, [xlsxAttachment, extraction, extracting, runExtraction]);

  const toggleRow = (rowIndex: number) => {
    setSelectedRows((prev) => {
      const next = new Set(prev);
      if (next.has(rowIndex)) next.delete(rowIndex);
      else next.add(rowIndex);
      return next;
    });
  };

  const selectAll = () => {
    if (!extraction) return;
    const all = new Set(
      extraction.rows.filter((r) => r.matched_id).map((r) => r.row_index),
    );
    setSelectedRows(all);
  };

  const selectNone = () => setSelectedRows(new Set());

  const handleBulkSubmit = async (channel: BulkChannel) => {
    if (!extraction || !ctx) return;
    if (selectedRows.size === 0) {
      setExtractError("Pick at least one manufacturer.");
      return;
    }
    if (!subject.trim() || !question.trim()) {
      setExtractError("Subject and question are required.");
      return;
    }

    const targets = extraction.rows
      .filter((r) => selectedRows.has(r.row_index) && r.matched_id != null)
      .map((r) => ({
        manufacturer_id: r.matched_id as number,
        source_excel_row: r.row_index,
      }));

    if (targets.length === 0) {
      setExtractError("None of the selected rows have a matched manufacturer in your DB.");
      return;
    }

    let testCallTo: string | null = null;
    if (channel === "test_call") {
      const digits = digitsOnly(testLocal);
      if (digits.length < 7) {
        setExtractError("Enter a valid phone number for the test call (at least 7 digits).");
        return;
      }
      testCallTo = `${testCountryCode}${digits}`;
    }

    setSubmitting(channel);
    setExtractError(null);
    try {
      const result = await api.inquiries.bulkCreate({
        targets,
        subject: subject.trim(),
        question: question.trim(),
        fallback_after_hours: fallbackHours,
        source_inquiry_uuid: ctx.uuid,
        // Prefer our own S3 mirror so the response-writeback path is
        // independent of InpharmD's 10-second signed URLs.
        source_excel_url:
          extraction.excel_s3_url ?? xlsxAttachment?.doc_url ?? null,
        source_excel_sheet: extraction.sheet_name,
        dispatch_channel: channel,
        test_call_to_number: testCallTo,
      });
      setBulkResult(result);
      if (channel === "test_call") {
        setBanner(
          result.test_call_to
            ? `Test call dialing ${result.test_call_to} — drafts saved for ${result.created.length} manufacturer${result.created.length === 1 ? "" : "s"}.`
            : `Test call attempted — drafts saved for ${result.created.length} manufacturer${result.created.length === 1 ? "" : "s"}.`,
        );
      } else if (channel === "call") {
        setBanner(
          `Calling ${result.dispatched ?? 0} manufacturer${result.dispatched === 1 ? "" : "s"}` +
            (result.failed.length > 0 ? ` · ${result.failed.length} skipped` : ""),
        );
      } else {
        setBanner(
          `Emailed ${result.dispatched ?? result.created.length} manufacturer${result.created.length === 1 ? "" : "s"}` +
            (result.failed.length > 0 ? ` · ${result.failed.length} failed` : ""),
        );
      }
    } catch (e: any) {
      setExtractError(e?.message ?? "Bulk dispatch failed.");
    } finally {
      setSubmitting(null);
    }
  };

  const contextNote = useMemo(() => {
    if (!ctx?.uuid && !ctx?.title) return undefined;
    const idChunk = ctx.uuid ? ` (${ctx.uuid.slice(0, 8)}…)` : "";
    return `Forwarding InpharmD inquiry${idChunk} — pick a manufacturer and submit.`;
  }, [ctx]);

  if (!ctx) {
    return (
      <section className="page-head">
        <h1>Contact Manufacturer</h1>
        <p>
          No inquiry context — open an inquiry from the{" "}
          <a href="#platform-inquiries">InpharmD Inquiries</a> tab and click
          "Contact manufacturer".
        </p>
      </section>
    );
  }

  return (
    <>
      <section className="page-head">
        <button
          type="button"
          className="btn btn-ghost back-btn"
          onClick={handleCancel}
        >
          ← Back to InpharmD Inquiries
        </button>
        <h1>Contact Manufacturer</h1>
        <p>
          Forward this inquiry to one or more manufacturers. Once you submit,
          we'll email each manufacturer and track their responses here.
        </p>
      </section>

      {/* Inquiry context card */}
      <div className="contact-context-card">
        <div className="contact-context-row">
          <span className="contact-context-label">Inquiry</span>
          <span className="contact-context-value">{ctx.title || "(no title)"}</span>
        </div>
        {ctx.submitter && (
          <div className="contact-context-row">
            <span className="contact-context-label">Submitter</span>
            <span className="contact-context-value">{ctx.submitter}</span>
          </div>
        )}
        {ctx.type && (
          <div className="contact-context-row">
            <span className="contact-context-label">Type</span>
            <span className="contact-context-value">
              <span className="ext-chip ext-chip-info">{ctx.type}</span>
            </span>
          </div>
        )}
        {ctx.uuid && (
          <div className="contact-context-row">
            <span className="contact-context-label">UUID</span>
            <span className="contact-context-value mono-small">{ctx.uuid}</span>
          </div>
        )}
        {ctx.attachments && ctx.attachments.length > 0 && (
          <div className="contact-context-row">
            <span className="contact-context-label">
              Attachments ({ctx.attachments.length})
            </span>
            <span className="contact-context-value">
              <ul className="contact-attachment-list">
                {ctx.attachments.map((a) => (
                  <li key={a.id}>
                    <a href={a.doc_url} target="_blank" rel="noreferrer">
                      📎 {a.file_name}
                    </a>
                  </li>
                ))}
              </ul>
            </span>
          </div>
        )}
      </div>

      {/* Auto-detect status banner — appears only while detecting or after
          a successful detection. No button: detection runs on mount. */}
      {xlsxAttachment && !bulkResult && (extracting || extraction) && (
        <div className="dispatch-mode-card">
          <div className="dispatch-mode-head">
            <strong>
              {extracting
                ? "Reading spreadsheet…"
                : extraction
                ? `Detected ${extraction.matched} of ${extraction.total} manufacturers`
                : ""}
            </strong>
            <span className="dispatch-mode-sub">
              From <em>{xlsxAttachment.file_name}</em>
              {extraction && (
                <>
                  {" "}· sheet "{extraction.sheet_name}", column
                  {" "}"{extraction.header_value.replace(/\n/g, " ")}"
                </>
              )}
            </span>
          </div>
          {extraction && (
            <div className="dispatch-mode-actions">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  setMode("single");
                  setExtraction(null);
                  setSelectedRows(new Set());
                }}
                title="Pick a single manufacturer manually instead"
              >
                Manual instead
              </button>
            </div>
          )}
        </div>
      )}

      {extractError && <div className="error-banner">{extractError}</div>}
      {error && <div className="error-banner">{error}</div>}

      {/* MULTI mode: detected manufacturers + bulk form */}
      {mode === "multi" && extraction && !bulkResult && (() => {
        const searchTokens = search.trim().toLowerCase();
        const filteredRows = searchTokens
          ? extraction.rows.filter((r) => {
              const hay = `${r.raw_name} ${r.matched_name ?? ""}`.toLowerCase();
              return hay.includes(searchTokens);
            })
          : extraction.rows;
        const visibleSelectedCount = filteredRows.filter((r) =>
          selectedRows.has(r.row_index),
        ).length;
        return (
        <>
          <div className="page-form">
            <div className="page-form-header">
              <h2>Detected manufacturers</h2>
              <div className="mfr-detect-meta">
                {extraction.matched} of {extraction.total} matched to your DB
                <span className="cell-muted">
                  {" "}· sheet "{extraction.sheet_name}", column "{extraction.header_value}"
                </span>
              </div>
            </div>
            <div className="page-form-body">
              <div className="bulk-search-row">
                <span className="bulk-search-icon" aria-hidden>🔍</span>
                <input
                  type="search"
                  className="bulk-search-input"
                  placeholder="Search manufacturers (e.g. Pfizer, Teva)…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {search && (
                  <button
                    type="button"
                    className="btn-link bulk-search-clear"
                    onClick={() => setSearch("")}
                  >
                    Clear
                  </button>
                )}
              </div>
              <div className="bulk-select-toolbar">
                <button type="button" className="btn-link" onClick={selectAll}>
                  Select all matched ({extraction.matched})
                </button>
                <button type="button" className="btn-link" onClick={selectNone}>
                  Clear
                </button>
                <span className="cell-muted">
                  {selectedRows.size} selected
                  {search && filteredRows.length !== extraction.rows.length && (
                    <>
                      {" "}· showing {filteredRows.length} of {extraction.rows.length}
                      {visibleSelectedCount !== selectedRows.size &&
                        ` (${visibleSelectedCount} visible selected)`}
                    </>
                  )}
                </span>
              </div>
              <div className="bulk-row-list">
                {filteredRows.length === 0 && (
                  <div className="empty">
                    <div className="empty-title">No manufacturers match "{search}".</div>
                  </div>
                )}
                {filteredRows.map((r) => {
                  const checked = selectedRows.has(r.row_index);
                  const matched = r.matched_id != null;
                  const mfr = r.matched_id ? mfrById[r.matched_id] : undefined;
                  const email = mfr?.official_mi_email || mfr?.team_verified_email;
                  return (
                    <label
                      key={r.row_index}
                      className={`bulk-row ${checked ? "bulk-row-checked" : ""} ${
                        matched ? "" : "bulk-row-unmatched"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!matched}
                        onChange={() => toggleRow(r.row_index)}
                      />
                      <div className="bulk-row-main">
                        <div className="bulk-row-name">
                          <span className="bulk-row-raw">{r.raw_name}</span>
                          {matched && (
                            <span className={`ext-chip ext-chip-${confidenceTone(r.confidence)} ext-chip-static bulk-row-chip`}>
                              {confidenceLabel(r.confidence)}
                            </span>
                          )}
                        </div>
                        {matched ? (
                          <div className="bulk-row-mfr">
                            <span className="bulk-row-mfr-name">{r.matched_name}</span>
                            {mfr?.parent_owner && mfr.parent_owner !== r.matched_name && (
                              <span className="cell-muted">· {mfr.parent_owner}</span>
                            )}
                          </div>
                        ) : (
                          <div className="bulk-row-mfr">
                            <span className="cell-muted">No match in your manufacturer DB — add it first</span>
                          </div>
                        )}
                        {matched && (() => {
                          const inHours = isWithinBusinessHoursNow(mfr?.mi_phone_hours);
                          return (
                            <div className="bulk-row-mfr-meta">
                              {email && <span>📧 {email}</span>}
                              {mfr?.mi_phone && <span>📞 {mfr.mi_phone}</span>}
                              {mfr?.mi_phone_hours && (
                                <span
                                  className={
                                    inHours === false
                                      ? "bulk-row-hours bulk-row-hours-out"
                                      : inHours === true
                                      ? "bulk-row-hours bulk-row-hours-in"
                                      : "bulk-row-hours"
                                  }
                                  title={
                                    inHours === false
                                      ? "Outside business hours right now — Call Agent will skip this number"
                                      : inHours === true
                                      ? "Inside business hours right now"
                                      : undefined
                                  }
                                >
                                  🕒 {mfr.mi_phone_hours}
                                  {inHours === false && " · outside hours now"}
                                </span>
                              )}
                              {mfr?.typical_response_sla && (
                                <span>⏱ {mfr.typical_response_sla}</span>
                              )}
                              {!email && (
                                <span className="bulk-row-warn">No email on file — won't send</span>
                              )}
                            </div>
                          );
                        })()}
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Bulk subject + question + fallback */}
          <div className="page-form">
            <div className="page-form-header">
              <h2>Inquiry text</h2>
            </div>
            <div className="page-form-body">
              <div className="form-grid">
                <div className="field full">
                  <label>Subject<span className="req">*</span></label>
                  <input
                    type="text"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    placeholder="Subject line for every recipient"
                  />
                </div>
                <div className="field full">
                  <label>Question / Details<span className="req">*</span></label>
                  <textarea
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    rows={5}
                  />
                </div>
                <div className="field full">
                  <label>If no email response within</label>
                  <select
                    className="filter-select"
                    value={fallbackHours}
                    onChange={(e) => setFallbackHours(Number(e.target.value))}
                  >
                    <option value={12}>12 hours</option>
                    <option value={24}>24 hours</option>
                    <option value={48}>48 hours</option>
                    <option value={72}>3 days</option>
                    <option value={168}>7 days</option>
                  </select>
                </div>
              </div>
            </div>
          </div>

          {/* Send to manufacturer: three channels */}
          {(() => {
            // Count selected rows by reachability so each card shows what it
            // can actually do for THIS selection (e.g. "5 of 6 have email").
            const selectedMatched = extraction.rows.filter(
              (r) => selectedRows.has(r.row_index) && r.matched_id != null,
            );
            const reachableByEmail = selectedMatched.filter((r) => {
              const m = r.matched_id ? mfrById[r.matched_id] : undefined;
              return !!(m?.official_mi_email || m?.team_verified_email);
            }).length;
            const phoneRows = selectedMatched.filter((r) => {
              const m = r.matched_id ? mfrById[r.matched_id] : undefined;
              return !!m?.mi_phone;
            });
            const reachableByPhone = phoneRows.length;
            // How many of those have hours we can determine to be out-of-window
            // right now. Treat null (unknown) as still-callable since the
            // backend will fall back to its own check.
            const outOfHoursNow = phoneRows.filter((r) => {
              const m = r.matched_id ? mfrById[r.matched_id] : undefined;
              return isWithinBusinessHoursNow(m?.mi_phone_hours) === false;
            }).length;
            const callableNow = reachableByPhone - outOfHoursNow;
            const total = selectedMatched.length;
            const noneSelected = total === 0;
            const testDigits = digitsOnly(testLocal);
            const testValid = testDigits.length >= 7;
            const anyBusy = submitting !== null;

            return (
              <div className="page-form">
                <div className="page-form-header">
                  <h2>Send to manufacturer</h2>
                  <div className="mfr-detect-meta">
                    {noneSelected
                      ? "Pick at least one manufacturer above."
                      : `${total} selected · choose how to reach them`}
                  </div>
                </div>
                <div className="page-form-body">
                  <div className="channel-grid">
                    {/* Email card */}
                    <div className={`channel-card ${reachableByEmail === 0 ? "channel-disabled" : ""}`}>
                      <div className="channel-icon channel-icon-email">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="3" y="5" width="18" height="14" rx="2" />
                          <path d="m3 7 9 6 9-6" />
                        </svg>
                      </div>
                      <div className="channel-title">Send Email</div>
                      <div className="channel-sub">
                        Email all {total} selected manufacturer{total === 1 ? "" : "s"}.
                        Voice agent will call any that don't reply within{" "}
                        <strong>{fallbackHours}h</strong>.
                      </div>
                      <ul className="channel-meta">
                        <li>
                          <span>Reachable</span> {reachableByEmail} of {total} have email
                        </li>
                        <li>
                          <span>Fallback</span> agent call after {fallbackHours}h
                        </li>
                      </ul>
                      <button
                        className="btn btn-primary"
                        type="button"
                        disabled={noneSelected || reachableByEmail === 0 || anyBusy}
                        onClick={() => handleBulkSubmit("email")}
                      >
                        {submitting === "email"
                          ? "Sending…"
                          : `Send Email to ${reachableByEmail || total}`}
                      </button>
                    </div>

                    {/* Call card */}
                    <div className={`channel-card ${callableNow === 0 ? "channel-disabled" : ""}`}>
                      <div className="channel-icon channel-icon-call">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.86 19.86 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.86 19.86 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92Z" />
                        </svg>
                      </div>
                      <div className="channel-title">Call Agent</div>
                      <div className="channel-sub">
                        Voice agent dials each callable manufacturer in sequence.
                        Numbers outside their business hours right now stay as
                        drafts — you can retry them later from Outreach.
                      </div>
                      <ul className="channel-meta">
                        <li>
                          <span>Callable now</span>{" "}
                          {callableNow} of {total}
                          {reachableByPhone < total && (
                            <span className="cell-muted">
                              {" "}· {total - reachableByPhone} no phone
                            </span>
                          )}
                          {outOfHoursNow > 0 && (
                            <span className="bulk-row-warn">
                              {" "}· {outOfHoursNow} outside hours
                            </span>
                          )}
                        </li>
                        <li>
                          <span>Order</span> sequentially, one at a time
                        </li>
                      </ul>
                      <button
                        className="btn btn-primary"
                        type="button"
                        disabled={noneSelected || callableNow === 0 || anyBusy}
                        title={
                          callableNow === 0 && reachableByPhone > 0
                            ? "All selected manufacturers are outside business hours right now."
                            : callableNow === 0
                            ? "No selected manufacturers have a phone number on file."
                            : undefined
                        }
                        onClick={() => handleBulkSubmit("call")}
                      >
                        {submitting === "call"
                          ? "Calling…"
                          : callableNow === 0
                          ? "Nobody callable now"
                          : `Call ${callableNow} Now`}
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
                        Dial <strong>your own number</strong> with the first
                        selected manufacturer's context. Lets you hear the
                        script before contacting any real MI desk —{" "}
                        <strong>no manufacturer is called</strong>. Drafts are
                        still saved for all {total} selected.
                      </div>
                      <div className="phone-input-row">
                        <select
                          className="phone-cc-select"
                          value={testCountryCode}
                          onChange={(e) => setTestCountryCode(e.target.value)}
                          disabled={anyBusy}
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
                          disabled={anyBusy}
                        />
                      </div>
                      <div className="channel-test-hint">
                        Dialing:{" "}
                        <strong>
                          {testValid ? `${testCountryCode}${testDigits}` : "—"}
                        </strong>
                      </div>
                      <button
                        className="btn btn-primary"
                        type="button"
                        disabled={noneSelected || !testValid || anyBusy}
                        onClick={() => handleBulkSubmit("test_call")}
                      >
                        {submitting === "test_call" ? "Dialing…" : "Call My Number"}
                      </button>
                    </div>
                  </div>
                </div>
                <div className="page-form-footer">
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={handleCancel}
                    disabled={anyBusy}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            );
          })()}
        </>
        );
      })()}

      {/* SINGLE mode: existing form */}
      {mode === "single" && !bulkResult && (
        loadingMfrs ? (
          <div className="empty">
            <div className="empty-title">Loading manufacturers…</div>
          </div>
        ) : (
          <InquiryForm
            manufacturers={manufacturers}
            defaultSubject={subject}
            defaultQuestion={question}
            contextNote={contextNote}
            variant="page"
            title="Contact Manufacturer"
            submitLabel="Create & choose channel"
            onClose={handleCancel}
            onSubmit={handleSingleCreate}
          />
        )
      )}

      {/* Bulk result summary */}
      {bulkResult && (
        <div className="page-form">
          <div className="page-form-header">
            <h2>Dispatch summary</h2>
          </div>
          <div className="page-form-body">
            <div className="bulk-result-grid">
              <div className="bulk-result-stat bulk-result-good">
                <div className="bulk-result-num">{bulkResult.created.length}</div>
                <div className="bulk-result-label">Inquiries created</div>
              </div>
              {bulkResult.dispatch_channel === "email" && (
                <div className="bulk-result-stat">
                  <div className="bulk-result-num">
                    {bulkResult.created.filter((c) => c.status === "email_sent").length}
                  </div>
                  <div className="bulk-result-label">Emails sent</div>
                </div>
              )}
              {bulkResult.dispatch_channel === "call" && (
                <div className="bulk-result-stat">
                  <div className="bulk-result-num">
                    {bulkResult.created.filter((c) => c.status === "call_pending").length}
                  </div>
                  <div className="bulk-result-label">Calls placed</div>
                </div>
              )}
              {bulkResult.dispatch_channel === "test_call" && (
                <div className="bulk-result-stat">
                  <div className="bulk-result-num">{bulkResult.dispatched ?? 0}</div>
                  <div className="bulk-result-label">
                    Test call to {bulkResult.test_call_to ?? "your number"}
                  </div>
                </div>
              )}
              {bulkResult.failed.length > 0 && (
                <div className="bulk-result-stat bulk-result-bad">
                  <div className="bulk-result-num">{bulkResult.failed.length}</div>
                  <div className="bulk-result-label">
                    {bulkResult.dispatch_channel === "call" ? "Skipped / failed" : "Failed"}
                  </div>
                </div>
              )}
            </div>
            {bulkResult.created.length > 0 && (
              <>
                <h4>Created inquiries</h4>
                <ul className="bulk-result-list">
                  {bulkResult.created.map((c) => (
                    <li key={c.id}>
                      <strong>#{c.id}</strong> — {c.manufacturer?.manufacturer ?? `mfr ${c.manufacturer_id}`}
                      {" · "}
                      <span className="cell-muted">{c.status}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {bulkResult.failed.length > 0 && (
              <>
                <h4>Failed</h4>
                <ul className="bulk-result-list bulk-result-list-bad">
                  {bulkResult.failed.map((f, i) => (
                    <li key={i}>
                      <strong>mfr {f.manufacturer_id}</strong>: {f.error}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
          <div className="page-form-footer">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => goTo("platform-inquiries")}
            >
              Back to InpharmD Inquiries
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => goTo("inquiries")}
            >
              View in Manufacturer Outreach →
            </button>
          </div>
        </div>
      )}

      {pendingChoice && (
        <ChannelChooser
          inquiry={pendingChoice}
          onClose={() => {
            setPendingChoice(null);
            goTo("inquiries");
          }}
          onSendEmail={async () => {
            try {
              await api.inquiries.sendEmail(pendingChoice.id);
              setBanner(
                `Email queued for ${pendingChoice.manufacturer?.manufacturer ?? "manufacturer"}.`,
              );
            } catch (e: any) {
              setBanner(`Failed to send email: ${e?.message ?? "unknown"}`);
            }
            setPendingChoice(null);
            goTo("inquiries");
          }}
          onCallTriggered={() => {
            setBanner("Call placed — the agent is dialing now.");
            setPendingChoice(null);
            goTo("inquiries");
          }}
          onTestCallTriggered={(phone) => {
            setBanner(`Test call dialing ${phone}.`);
          }}
        />
      )}

      {banner && (
        <div className="success-banner toast-banner" role="status">
          {banner}
        </div>
      )}
    </>
  );
}

// Helper exported so the InpharmD Inquiries page can stash context before
// navigating here.
export function startContactManufacturerFlow(ctx: ForwardContext): void {
  try {
    sessionStorage.setItem(CTX_KEY, JSON.stringify(ctx));
  } catch {
    /* sessionStorage full / unavailable — fall back to URL params */
  }
  const qs = new URLSearchParams();
  if (ctx.uuid) qs.set("uuid", ctx.uuid);
  if (ctx.title) qs.set("title", ctx.title);
  window.location.hash = `contact-manufacturer?${qs.toString()}`;
}
