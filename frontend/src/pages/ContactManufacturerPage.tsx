import { useCallback, useEffect, useMemo, useState } from "react";
import InquiryForm from "../components/InquiryForm";
import ChannelChooser from "../components/ChannelChooser";
import ManufacturerForm from "../components/ManufacturerForm";
import StatusBadge from "../components/StatusBadge";
import { api } from "../api";
import { isWithinBusinessHoursNow } from "../utils/businessHours";
import type {
  Inquiry,
  InquiryInput,
  ManufacturerContact,
  ManufacturerContactInput,
} from "../types";
import { INQUIRY_SUBJECT_MAX_LENGTH } from "../types";

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
  medication_name: string;
  pi_storage: string;
  ndc: string;
  pi_link: string;
}

interface DetectedExtraction {
  sheet_name: string;
  header_row: number;
  header_value: string;
  total: number;
  matched: number;
  medication_col_header: string | null;
  pi_storage_col_header: string | null;
  ndc_col_header: string | null;
  excel_s3_url: string | null;
  rows: DetectedRow[];
}

interface AttachmentExtractionState {
  att: Attachment;
  result: DetectedExtraction | null;
  extracting: boolean;
  error: string | null;
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
  // Fall back to URL params (page was refreshed — sessionStorage is gone).
  const params = readQuery();
  const uuid = params.get("uuid") ?? "";
  const title = params.get("title") ?? "";
  if (!uuid && !title) return null;

  // Reconstruct attachments from indexed URL params (new format: att_url_0, att_url_1, …)
  // with fallback to legacy single-attachment params (att_url, att_name).
  const attachments: Attachment[] = [];
  let i = 0;
  while (true) {
    const url = params.get(`att_url_${i}`);
    const name = params.get(`att_name_${i}`);
    if (!url || !name) break;
    attachments.push({ id: i, file_name: name, doc_url: url });
    i++;
  }
  if (attachments.length === 0) {
    const attUrl = params.get("att_url");
    const attName = params.get("att_name");
    if (attUrl && attName) attachments.push({ id: 0, file_name: attName, doc_url: attUrl });
  }
  return { uuid, title, attachments };
};

const goTo = (hash: string) => {
  window.location.hash = hash;
};

// Accept both .xlsx and .csv — backend extract handles both formats.
const isExtractable = (a: Attachment): boolean =>
  /\.(xlsx|csv)(\?|$)/i.test(a.file_name) ||
  /\.(xlsx|csv)(\?|$)/i.test(a.doc_url);

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

interface ReviewCard {
  rowIndex: number;
  manufacturerId: number;
  manufacturerName: string;
  toEmail: string;
  subject: string;
  question: string;
  medicationName: string | null;
  piStorageData: string | null;
  piLink: string | null;
  sentInquiryId: number | null;
  sending: boolean;
  error: string | null;
  // Which source file this row belongs to — needed for per-file writeback.
  excelS3Url: string | null;
  excelSheetName: string | null;
}

const COUNTRY_CODES: { code: string; label: string }[] = [
  { code: "+1", label: "+1 US / CA" },
  { code: "+91", label: "+91 IN" },
  { code: "+44", label: "+44 UK" },
  { code: "+61", label: "+61 AU" },
  { code: "+49", label: "+49 DE" },
];

const digitsOnly = (s: string) => s.replace(/\D+/g, "");

// selectedKeys format: `${attIdx}:${rowIndex}` — namespaces row indices across files.
const selKey = (attIdx: number, rowIndex: number) => `${attIdx}:${rowIndex}`;

export default function ContactManufacturerPage() {
  const [ctx] = useState<ForwardContext | null>(readContext);
  const [manufacturers, setManufacturers] = useState<ManufacturerContact[]>([]);
  const [loadingMfrs, setLoadingMfrs] = useState(true);
  const [existingInquiries, setExistingInquiries] = useState<Inquiry[]>([]);
  const [pendingChoice, setPendingChoice] = useState<Inquiry | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // "Add manufacturer" modal
  const [addingMfrName, setAddingMfrName] = useState<string | null>(null);

  // Multi-dispatch state
  const [mode, setMode] = useState<Mode>("single");
  const [attachmentExtractions, setAttachmentExtractions] = useState<AttachmentExtractionState[]>([]);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [manualOverride, setManualOverride] = useState(false);
  // selectedKeys: `${attIdx}:${rowIndex}` for every checked row across all files.
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [subject, setSubject] = useState("");
  const [question, setQuestion] = useState("");
  const [fallbackHours, setFallbackHours] = useState(24);
  const [submitting, setSubmitting] = useState<BulkChannel | null>(null);
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
  const [sendingAll, setSendingAll] = useState(false);

  // emailReview is persisted to sessionStorage so the green "sent" status
  // survives a page refresh. Keyed by inquiry UUID so it doesn't bleed
  // across different inquiries.
  const emailReviewKey = ctx?.uuid ? `inpharmd:email-review:${ctx.uuid}` : null;
  const [emailReview, setEmailReview] = useState<ReviewCard[] | null>(() => {
    if (!ctx?.uuid) return null;
    try {
      const raw = sessionStorage.getItem(`inpharmd:email-review:${ctx.uuid}`);
      if (raw) {
        const parsed = JSON.parse(raw) as ReviewCard[];
        // A refresh means no HTTP requests are in flight — clear sending flag.
        return parsed.map((c) => ({ ...c, sending: false }));
      }
    } catch { /* ignore */ }
    return null;
  });

  useEffect(() => {
    if (!emailReviewKey) return;
    if (emailReview === null) {
      sessionStorage.removeItem(emailReviewKey);
    } else {
      sessionStorage.setItem(emailReviewKey, JSON.stringify(emailReview));
    }
  }, [emailReview, emailReviewKey]);

  const reloadManufacturers = useCallback(() => {
    api.manufacturers
      .list()
      .then((data) => { setManufacturers(data); setError(null); })
      .catch((e: any) => setError(e?.message ?? "Failed to load manufacturers."))
      .finally(() => setLoadingMfrs(false));
  }, []);

  useEffect(() => {
    reloadManufacturers();
    const onFocus = () => reloadManufacturers();
    const onVisible = () => {
      if (document.visibilityState === "visible") reloadManufacturers();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reloadManufacturers]);

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

  // All attachments that can be processed for manufacturer extraction.
  const extractableAttachments = useMemo(
    () => (ctx?.attachments ?? []).filter(isExtractable),
    [ctx],
  );

  const mfrById = useMemo(() => {
    const m: Record<number, ManufacturerContact> = {};
    for (const x of manufacturers) m[x.id] = x;
    return m;
  }, [manufacturers]);

  // Fetch any inquiries already sent for this source platform inquiry so we
  // can mark those manufacturers as "already contacted" in the picker.
  useEffect(() => {
    if (!ctx?.uuid) return;
    api.inquiries
      .list({ source_inquiry_uuid: ctx.uuid })
      .then(setExistingInquiries)
      .catch(() => {});
  }, [ctx?.uuid]);

  // Map manufacturer_id → inquiry for the first (most-recent) contact per mfr.
  const contactedMfrMap = useMemo(() => {
    const m = new Map<number, Inquiry>();
    for (const inq of existingInquiries) {
      if (!m.has(inq.manufacturer_id)) m.set(inq.manufacturer_id, inq);
    }
    return m;
  }, [existingInquiries]);

  // Remove contacted manufacturers from the current selection whenever the
  // map updates (e.g. after the fetch completes post-extraction).
  useEffect(() => {
    if (contactedMfrMap.size === 0) return;
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      attachmentExtractions.forEach((s, attIdx) => {
        s.result?.rows.forEach((r) => {
          if (r.matched_id != null && contactedMfrMap.has(r.matched_id)) {
            next.delete(selKey(attIdx, r.row_index));
          }
        });
      });
      return next;
    });
  }, [contactedMfrMap, attachmentExtractions]);

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

  const handleAddManufacturer = useCallback(async (data: ManufacturerContactInput) => {
    await api.manufacturers.create(data);
    setAddingMfrName(null);
    reloadManufacturers();
    // Re-run extraction so the newly added manufacturer appears matched.
    setAttachmentExtractions([]);
    setSelectedKeys(new Set());
  }, [reloadManufacturers]);

  const runExtractions = useCallback(async () => {
    if (extractableAttachments.length === 0) return;
    setExtractError(null);
    // Initialize per-file loading state.
    setAttachmentExtractions(
      extractableAttachments.map((att) => ({ att, result: null, extracting: true, error: null })),
    );
    setMode("multi");

    // Run all files in parallel; each updates its own slot as it completes.
    await Promise.all(
      extractableAttachments.map(async (att, attIdx) => {
        try {
          const [result] = await Promise.all([
            api.externalInquiries.extractManufacturers(att.doc_url, ctx?.uuid),
            api.manufacturers.list().then(setManufacturers).catch(() => {}),
          ]);
          setAttachmentExtractions((prev) =>
            prev.map((s, i) => (i === attIdx ? { ...s, result, extracting: false } : s)),
          );
          // Pre-select exact + partial matches from this file.
          setSelectedKeys((prev) => {
            const next = new Set(prev);
            for (const r of result.rows) {
              if (r.matched_id && (r.confidence === "exact" || r.confidence === "partial")) {
                next.add(selKey(attIdx, r.row_index));
              }
            }
            return next;
          });
        } catch (e: any) {
          setAttachmentExtractions((prev) =>
            prev.map((s, i) =>
              i === attIdx
                ? { ...s, extracting: false, error: e?.message ?? "Could not read manufacturers from the spreadsheet." }
                : s,
            ),
          );
        }
      }),
    );
  }, [extractableAttachments, ctx]);

  // Auto-run on mount when extractable attachments are present. Gated on
  // extractError to avoid a retry loop on persistent backend errors.
  useEffect(() => {
    const anyExtracting = attachmentExtractions.some((s) => s.extracting);
    const anyDone = attachmentExtractions.some((s) => s.result !== null);
    if (
      extractableAttachments.length === 0 ||
      anyExtracting ||
      anyDone ||
      extractError ||
      manualOverride ||
      emailReview !== null  // already in email-review — don't re-run
    ) return;
    runExtractions();
  }, [extractableAttachments, attachmentExtractions, extractError, manualOverride, emailReview, runExtractions]);

  const toggleRow = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectAll = () => {
    const next = new Set<string>();
    attachmentExtractions.forEach((s, attIdx) => {
      if (!s.result) return;
      s.result.rows
        .filter((r) => r.matched_id && !contactedMfrMap.has(r.matched_id))
        .forEach((r) => {
          next.add(selKey(attIdx, r.row_index));
        });
    });
    setSelectedKeys(next);
  };

  const selectNone = () => setSelectedKeys(new Set());

  const totalMatched = useMemo(
    () =>
      attachmentExtractions.reduce(
        (sum, s) => sum + (s.result?.matched ?? 0),
        0,
      ),
    [attachmentExtractions],
  );

  const totalContactable = useMemo(() => {
    let count = 0;
    attachmentExtractions.forEach((s) => {
      s.result?.rows.forEach((r) => {
        if (r.matched_id && !contactedMfrMap.has(r.matched_id)) count++;
      });
    });
    return count;
  }, [attachmentExtractions, contactedMfrMap]);

  const sendOne = async (card: ReviewCard, cardIndex: number) => {
    if (!ctx) return;
    setEmailReview((prev) =>
      prev ? prev.map((c, i) => i === cardIndex ? { ...c, sending: true, error: null } : c) : prev,
    );
    try {
      const result = await api.inquiries.bulkCreate({
        targets: [{
          manufacturer_id: card.manufacturerId,
          source_excel_row: card.rowIndex,
          // medication/PI already embedded in card.question — don't pass separately
          // or the backend's _build_body() would duplicate the product details section.
          medication_name: null,
          pi_storage_data: null,
          pi_link: card.piLink,
        }],
        subject: card.subject,
        question: card.question,
        fallback_after_hours: fallbackHours,
        source_inquiry_uuid: ctx.uuid,
        source_excel_url: card.excelS3Url,
        source_excel_sheet: card.excelSheetName,
        dispatch_channel: "none",
      });
      const inquiry = result.created[0];
      if (!inquiry) throw new Error(result.failed[0]?.error ?? "Failed to create inquiry");
      await api.inquiries.sendEmail(inquiry.id);
      setEmailReview((prev) =>
        prev ? prev.map((c, i) => i === cardIndex ? { ...c, sentInquiryId: inquiry.id, sending: false } : c) : prev,
      );
    } catch (e: any) {
      setEmailReview((prev) =>
        prev ? prev.map((c, i) => i === cardIndex ? { ...c, sending: false, error: e?.message ?? "Send failed" } : c) : prev,
      );
    }
  };

  const handleEnterEmailReview = () => {
    if (attachmentExtractions.length === 0 || !ctx) return;
    if (selectedKeys.size === 0) {
      setExtractError("Pick at least one manufacturer.");
      return;
    }
    if (!subject.trim() || !question.trim()) {
      setExtractError("Subject and question are required.");
      return;
    }
    if (subject.trim().length > INQUIRY_SUBJECT_MAX_LENGTH) {
      setExtractError(`Subject must be ${INQUIRY_SUBJECT_MAX_LENGTH} characters or fewer.`);
      return;
    }
    const cards: ReviewCard[] = [];
    attachmentExtractions.forEach((s, attIdx) => {
      if (!s.result) return;
      s.result.rows
        .filter((r) => {
          if (!selectedKeys.has(selKey(attIdx, r.row_index)) || r.matched_id == null) return false;
          const m = mfrById[r.matched_id];
          if (m) return !!(m.official_mi_email || m.team_verified_email);
          return true;
        })
        .forEach((r) => {
          const mfr = mfrById[r.matched_id as number];
          // Build the full per-card question mirroring what _build_body() does on the
          // backend, so the user sees (and can edit) the complete email content.
          const medName = r.medication_name || null;
          const piStorage = r.pi_storage || null;
          const piLink = r.pi_link || null;
          let fullQuestion = question.trim();
          if (medName || piStorage || piLink) {
            fullQuestion += "\n\nPRODUCT DETAILS:";
            if (medName) fullQuestion += `\nMedication/Vaccine: ${medName}`;
            if (piStorage) fullQuestion += `\nPI Storage Information: ${piStorage}`;
            if (piLink) fullQuestion += `\nPrescribing Information: ${piLink}`;
          }
          cards.push({
            rowIndex: r.row_index,
            manufacturerId: r.matched_id as number,
            manufacturerName: r.matched_name ?? "",
            toEmail: mfr?.official_mi_email || mfr?.team_verified_email || "",
            subject: subject.trim(),
            question: fullQuestion,
            medicationName: medName,
            piStorageData: piStorage,
            piLink,
            sentInquiryId: null,
            sending: false,
            error: null,
            excelS3Url: s.result!.excel_s3_url ?? s.att.doc_url,
            excelSheetName: s.result!.sheet_name,
          });
        });
    });
    if (cards.length === 0) {
      setExtractError("None of the selected manufacturers have an email address on file.");
      return;
    }
    setExtractError(null);
    setEmailReview(cards);
  };

  const handleSendAll = async () => {
    if (!emailReview) return;
    setSendingAll(true);
    const unsent = emailReview
      .map((c, i) => ({ c, i }))
      .filter(({ c }) => c.sentInquiryId === null && !c.sending);
    for (const { c, i } of unsent) {
      await sendOne(c, i);
    }
    setSendingAll(false);
  };

  const handleBulkSubmit = async (channel: BulkChannel) => {
    if (attachmentExtractions.length === 0 || !ctx) return;
    if (selectedKeys.size === 0) {
      setExtractError("Pick at least one manufacturer.");
      return;
    }
    if (!subject.trim() || !question.trim()) {
      setExtractError("Subject and question are required.");
      return;
    }
    if (subject.trim().length > INQUIRY_SUBJECT_MAX_LENGTH) {
      setExtractError(`Subject must be ${INQUIRY_SUBJECT_MAX_LENGTH} characters or fewer.`);
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
      // Group selected rows by source file and make one bulkCreate per file
      // so each inquiry gets the correct source_excel_url for response writeback.
      const byFile: Array<{
        s: AttachmentExtractionState;
        targets: { manufacturer_id: number; source_excel_row: number; medication_name: string | null; pi_storage_data: string | null; pi_link: string | null }[];
      }> = [];

      attachmentExtractions.forEach((s, attIdx) => {
        if (!s.result) return;
        const targets = s.result.rows
          .filter((r) => {
            if (!selectedKeys.has(selKey(attIdx, r.row_index)) || r.matched_id == null) return false;
            const m = mfrById[r.matched_id];
            if (m) {
              if (channel === "email") return !!(m.official_mi_email || m.team_verified_email);
              if (channel === "call") return !!m.mi_phone;
            }
            return true;
          })
          .map((r) => ({
            manufacturer_id: r.matched_id as number,
            source_excel_row: r.row_index,
            medication_name: r.medication_name || null,
            pi_storage_data: r.pi_storage || null,
            pi_link: r.pi_link || null,
          }));
        if (targets.length > 0) byFile.push({ s, targets });
      });

      if (byFile.length === 0) {
        setExtractError("None of the selected rows have a matched manufacturer in your DB.");
        return;
      }

      const allCreated: Inquiry[] = [];
      const allFailed: { manufacturer_id: number; error: string }[] = [];
      let totalDispatched = 0;
      let mergedResult: typeof bulkResult = null;

      for (let fileIdx = 0; fileIdx < byFile.length; fileIdx++) {
        const { s, targets } = byFile[fileIdx];
        const result = await api.inquiries.bulkCreate({
          targets,
          subject: subject.trim(),
          question: question.trim(),
          fallback_after_hours: fallbackHours,
          source_inquiry_uuid: ctx.uuid,
          source_excel_url: s.result!.excel_s3_url ?? s.att.doc_url ?? null,
          source_excel_sheet: s.result!.sheet_name,
          dispatch_channel: channel,
          // Only the first file fires the test call — all others become drafts.
          test_call_to_number: fileIdx === 0 ? testCallTo : null,
        });
        allCreated.push(...result.created);
        allFailed.push(...result.failed);
        totalDispatched += result.dispatched ?? 0;
        if (fileIdx === 0) mergedResult = result;
      }

      const merged = {
        created: allCreated,
        failed: allFailed,
        dispatch_channel: channel,
        dispatched: totalDispatched,
        test_call_inquiry_id: mergedResult?.test_call_inquiry_id ?? null,
        test_call_to: mergedResult?.test_call_to ?? null,
      };
      setBulkResult(merged);

      if (channel === "test_call") {
        setBanner(
          merged.test_call_to
            ? `Test call dialing ${merged.test_call_to} — drafts saved for ${allCreated.length} manufacturer${allCreated.length === 1 ? "" : "s"}.`
            : `Test call attempted — drafts saved for ${allCreated.length} manufacturer${allCreated.length === 1 ? "" : "s"}.`,
        );
      } else if (channel === "call") {
        setBanner(
          `Calling ${totalDispatched} manufacturer${totalDispatched === 1 ? "" : "s"}` +
            (allFailed.length > 0 ? ` · ${allFailed.length} skipped` : ""),
        );
      } else {
        const sent = allCreated.length;
        setBanner(
          `Emailed ${sent} manufacturer${sent === 1 ? "" : "s"}` +
            (allFailed.length > 0 ? ` · ${allFailed.length} failed` : ""),
        );
      }
    } catch (e: any) {
      setExtractError(e?.message ?? "Bulk dispatch failed.");
    } finally {
      setSubmitting(null);
    }
  };


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

  const anyExtracting = attachmentExtractions.some((s) => s.extracting);
  const anyExtracted = attachmentExtractions.some((s) => s.result !== null);

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

      {/* Per-file detection banners */}
      {extractableAttachments.length > 0 && !bulkResult && (anyExtracting || anyExtracted) &&
        attachmentExtractions.map((s, attIdx) => (
          <div className="dispatch-mode-card" key={s.att.id || attIdx}>
            <div className="dispatch-mode-head">
              <strong>
                {s.extracting
                  ? "Reading spreadsheet…"
                  : s.result
                  ? `Detected ${s.result.matched} of ${s.result.total} manufacturers`
                  : s.error
                  ? "Could not read spreadsheet"
                  : ""}
              </strong>
              <span className="dispatch-mode-sub">
                From <em>{s.att.file_name}</em>
                {s.result && (
                  <>
                    {" "}· sheet "{s.result.sheet_name}", column
                    {" "}"{s.result.header_value.replace(/\n/g, " ")}"
                    {s.result.medication_col_header
                      ? <> · 💊 med col: "{s.result.medication_col_header}"</>
                      : <> · <span style={{color:"#b45309"}}>⚠ no Medication/Vaccine Name column found</span></>
                    }
                    {s.result.pi_storage_col_header
                      ? <> · 🌡 PI col: "{s.result.pi_storage_col_header}"</>
                      : <> · <span style={{color:"#b45309"}}>⚠ no PI Storage column found</span></>
                    }
                    {s.result.ndc_col_header && (
                      <> · 💊 NDC col: "{s.result.ndc_col_header}" (DailyMed lookup enabled)</>
                    )}
                  </>
                )}
                {s.error && (
                  <span style={{ color: "#b45309", marginLeft: 4 }}>{s.error}</span>
                )}
              </span>
            </div>
            {anyExtracted && attIdx === 0 && (
              <div className="dispatch-mode-actions">
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => {
                    setManualOverride(true);
                    setMode("single");
                    setAttachmentExtractions([]);
                    setSelectedKeys(new Set());
                  }}
                  title="Pick a single manufacturer manually instead"
                >
                  Manual instead
                </button>
              </div>
            )}
          </div>
        ))
      }

      {extractError && <div className="error-banner">{extractError}</div>}
      {error && <div className="error-banner">{error}</div>}

      {/* MULTI mode: manufacturer lists grouped by file */}
      {mode === "multi" && anyExtracted && !bulkResult && !emailReview && (() => {
        const searchTokens = search.trim().toLowerCase();

        // Aggregate counts across all files for toolbar.
        const totalSelectedCount = selectedKeys.size;

        return (
          <>
            <div className="page-form">
              <div className="page-form-header">
                <h2>Detected manufacturers</h2>
                <div className="mfr-detect-meta">
                  {totalMatched} matched across {attachmentExtractions.filter(s => s.result).length} file{attachmentExtractions.filter(s => s.result).length !== 1 ? "s" : ""}
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
                    Select all matched ({totalContactable})
                  </button>
                  <button type="button" className="btn-link" onClick={selectNone}>
                    Clear
                  </button>
                  <span className="cell-muted">{totalSelectedCount} selected</span>
                  {contactedMfrMap.size > 0 && (
                    <span className="cell-muted">· {contactedMfrMap.size} already contacted</span>
                  )}
                </div>

                {/* One manufacturer list per source file */}
                {attachmentExtractions.map((s, attIdx) => {
                  if (!s.result) return null;
                  const filteredRows = searchTokens
                    ? s.result.rows.filter((r) => {
                        const hay = `${r.raw_name} ${r.matched_name ?? ""}`.toLowerCase();
                        return hay.includes(searchTokens);
                      })
                    : s.result.rows;
                  return (
                    <div key={s.att.id || attIdx} style={{ marginTop: attIdx > 0 ? "24px" : "0" }}>
                      {attachmentExtractions.filter(x => x.result).length > 1 && (
                        <div
                          className="cell-muted"
                          style={{ fontSize: "12px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: "8px", paddingBottom: "6px", borderBottom: "1px solid var(--line)" }}
                        >
                          📎 {s.att.file_name} · {s.result.matched}/{s.result.total} matched
                        </div>
                      )}
                      {(() => {
                        const uncontactedRows = filteredRows.filter(
                          (r) => !(r.matched_id != null && contactedMfrMap.has(r.matched_id)),
                        );
                        const alreadyContactedRows = filteredRows.filter(
                          (r) => r.matched_id != null && contactedMfrMap.has(r.matched_id),
                        );
                        return (
                          <>
                            <div className="bulk-row-list">
                              {uncontactedRows.length === 0 && filteredRows.length === 0 && (
                                <div className="empty">
                                  <div className="empty-title">No manufacturers match "{search}".</div>
                                </div>
                              )}
                              {uncontactedRows.length === 0 && filteredRows.length > 0 && alreadyContactedRows.length > 0 && (
                                <div className="empty">
                                  <div className="empty-title" style={{ fontSize: "13px" }}>
                                    All matched manufacturers have already been contacted.
                                  </div>
                                </div>
                              )}
                              {uncontactedRows.map((r) => {
                                const key = selKey(attIdx, r.row_index);
                                const checked = selectedKeys.has(key);
                                const matched = r.matched_id != null;
                                const mfr = r.matched_id ? mfrById[r.matched_id] : undefined;
                                const email = mfr?.official_mi_email || mfr?.team_verified_email;
                                return (
                                  <label
                                    key={key}
                                    className={`bulk-row ${checked ? "bulk-row-checked" : ""} ${
                                      matched ? "" : "bulk-row-unmatched"
                                    }`}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={checked}
                                      disabled={!matched}
                                      onChange={() => toggleRow(key)}
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
                                          <span className="cell-muted">Not in manufacturer DB</span>
                                        </div>
                                      )}
                                      {(r.medication_name || r.pi_storage) && (
                                        <div className="bulk-row-product-info">
                                          {r.medication_name && (
                                            <span className="bulk-row-product-pill">💊 {r.medication_name}</span>
                                          )}
                                          {r.pi_storage && (
                                            <span className="bulk-row-product-pill">🌡 {r.pi_storage}</span>
                                          )}
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
                                    {!matched && (
                                      <button
                                        type="button"
                                        className="bulk-row-add-btn"
                                        onClick={(e) => {
                                          e.preventDefault();
                                          setAddingMfrName(r.raw_name);
                                        }}
                                      >
                                        + Add manufacturer
                                      </button>
                                    )}
                                  </label>
                                );
                              })}
                            </div>

                            {alreadyContactedRows.length > 0 && (
                              <div className="contacted-section">
                                <div className="contacted-section-header">
                                  Already contacted ({alreadyContactedRows.length})
                                </div>
                                {alreadyContactedRows.map((r) => {
                                  const inq = contactedMfrMap.get(r.matched_id!)!;
                                  return (
                                    <div key={r.row_index} className="contacted-row">
                                      <div className="contacted-row-main">
                                        <span className="contacted-row-name">
                                          {r.matched_name || r.raw_name}
                                        </span>
                                        {(r.medication_name || r.pi_storage) && (
                                          <div className="bulk-row-product-info" style={{ marginTop: 2 }}>
                                            {r.medication_name && (
                                              <span className="bulk-row-product-pill">💊 {r.medication_name}</span>
                                            )}
                                            {r.pi_storage && (
                                              <span className="bulk-row-product-pill">🌡 {r.pi_storage}</span>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                      <div className="contacted-row-status">
                                        <StatusBadge status={inq.status} />
                                        <span className="contacted-row-id">#{inq.id}</span>
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                          </>
                        );
                      })()}
                    </div>
                  );
                })}
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
                      maxLength={INQUIRY_SUBJECT_MAX_LENGTH}
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
              const allSelectedRows = attachmentExtractions.flatMap((s, attIdx) =>
                (s.result?.rows ?? []).filter((r) => selectedKeys.has(selKey(attIdx, r.row_index)) && r.matched_id != null),
              );
              const reachableByEmail = allSelectedRows.filter((r) => {
                const m = r.matched_id ? mfrById[r.matched_id] : undefined;
                return !!(m?.official_mi_email || m?.team_verified_email);
              }).length;
              const phoneRows = allSelectedRows.filter((r) => {
                const m = r.matched_id ? mfrById[r.matched_id] : undefined;
                return !!m?.mi_phone;
              });
              const reachableByPhone = phoneRows.length;
              const outOfHoursNow = phoneRows.filter((r) => {
                const m = r.matched_id ? mfrById[r.matched_id] : undefined;
                return isWithinBusinessHoursNow(m?.mi_phone_hours) === false;
              }).length;
              const callableNow = reachableByPhone - outOfHoursNow;
              const total = allSelectedRows.length;
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
                          onClick={handleEnterEmailReview}
                        >
                          {`Review & Send Email (${reachableByEmail || total})`}
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
                            <path d="M12 2v4" /><path d="M12 18v4" />
                            <path d="M4.93 4.93l2.83 2.83" /><path d="M16.24 16.24l2.83 2.83" />
                            <path d="M2 12h4" /><path d="M18 12h4" />
                            <path d="M4.93 19.07l2.83-2.83" /><path d="M16.24 7.76l2.83-2.83" />
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
                              <option key={c.code} value={c.code}>{c.label}</option>
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

      {/* EMAIL REVIEW mode */}
      {emailReview !== null && !bulkResult && (() => {
        const sentCount = emailReview.filter((c) => c.sentInquiryId !== null).length;
        const errCount = emailReview.filter((c) => c.error !== null && c.sentInquiryId === null).length;
        const pendingCount = emailReview.filter((c) => c.sentInquiryId === null).length;
        const allDone = pendingCount === 0;
        return (
          <div className="page-form">
            <div className="page-form-header">
              <h2>Review emails before sending</h2>
              <div className="mfr-detect-meta">
                {emailReview.length} email{emailReview.length !== 1 ? "s" : ""}
                {sentCount > 0 && <span style={{ color: "var(--green)", marginLeft: 6 }}>· {sentCount} sent</span>}
                {errCount > 0 && <span style={{ color: "var(--red)", marginLeft: 6 }}>· {errCount} failed</span>}
                {pendingCount > 0 && <span className="cell-muted" style={{ marginLeft: 6 }}>· {pendingCount} pending</span>}
              </div>
            </div>
            <div className="page-form-body">
              <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                {emailReview.map((card, i) => (
                  <div
                    key={card.rowIndex}
                    style={{
                      border: `1px solid ${card.sentInquiryId !== null ? "var(--green)" : card.error ? "#fecaca" : "var(--line)"}`,
                      borderRadius: "var(--radius)",
                      padding: "16px",
                      background: card.sentInquiryId !== null ? "var(--green-soft)" : card.error ? "var(--red-soft)" : "var(--surface)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px", gap: "12px" }}>
                      <div style={{ minWidth: 0 }}>
                        <span style={{ fontWeight: 700, fontSize: "14px" }}>{card.manufacturerName}</span>
                        {card.toEmail && (
                          <span style={{ color: "var(--muted)", fontSize: "12px", marginLeft: "8px" }}>
                            → {card.toEmail}
                          </span>
                        )}
                        {(card.medicationName || card.piStorageData || card.piLink) && (
                          <div style={{ marginTop: "6px", display: "flex", gap: "6px", flexWrap: "wrap" as const }}>
                            {card.medicationName && (
                              <span className="bulk-row-product-pill">💊 {card.medicationName}</span>
                            )}
                            {card.piStorageData && (
                              <span className="bulk-row-product-pill">🌡 {card.piStorageData}</span>
                            )}
                            {card.piLink && (
                              <a
                                href={card.piLink}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="bulk-row-product-pill"
                                style={{ textDecoration: "none" }}
                              >
                                📄 PI
                              </a>
                            )}
                          </div>
                        )}
                      </div>
                      <div style={{ flexShrink: 0 }}>
                        {card.sentInquiryId !== null && (
                          <span className="pill pill-green">✓ Sent #{card.sentInquiryId}</span>
                        )}
                        {card.sending && (
                          <span className="cell-muted" style={{ fontSize: "13px" }}>Sending…</span>
                        )}
                      </div>
                    </div>

                    <div className="form-grid">
                      <div className="field full">
                        <label style={{ fontWeight: 700 }}>Subject</label>
                        <input
                          type="text"
                          value={card.subject}
                          onChange={(e) => {
                            const val = e.target.value;
                            setEmailReview((prev) =>
                              prev ? prev.map((c, j) => j === i ? { ...c, subject: val } : c) : prev,
                            );
                          }}
                          disabled={card.sending || card.sentInquiryId !== null || sendingAll}
                          maxLength={INQUIRY_SUBJECT_MAX_LENGTH}
                        />
                      </div>
                      <div className="field full">
                        <label style={{ fontWeight: 700 }}>Question / Details</label>
                        <textarea
                          value={card.question}
                          onChange={(e) => {
                            const val = e.target.value;
                            setEmailReview((prev) =>
                              prev ? prev.map((c, j) => j === i ? { ...c, question: val } : c) : prev,
                            );
                          }}
                          rows={4}
                          disabled={card.sending || card.sentInquiryId !== null || sendingAll}
                        />
                      </div>
                    </div>

                    {card.error && (
                      <div className="error-banner" style={{ marginTop: "10px", marginBottom: 0, display: "flex", alignItems: "center", gap: "8px" }}>
                        <span style={{ flex: 1 }}>{card.error}</span>
                        <button
                          type="button"
                          className="btn-link"
                          onClick={() => sendOne(card, i)}
                          disabled={sendingAll}
                        >
                          Retry
                        </button>
                      </div>
                    )}

                    {card.sentInquiryId === null && !card.error && (
                      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "12px" }}>
                        <button
                          type="button"
                          className="btn btn-ghost"
                          disabled={card.sending || sendingAll}
                          onClick={() => sendOne(card, i)}
                        >
                          {card.sending ? "Sending…" : "Send"}
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
            <div className="page-form-footer">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setEmailReview(null)}
                disabled={sendingAll}
              >
                ← Edit selection
              </button>
              {allDone ? (
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    if (emailReviewKey) sessionStorage.removeItem(emailReviewKey);
                    goTo("inquiries");
                  }}
                >
                  View in Manufacturer Outreach →
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={sendingAll}
                  onClick={handleSendAll}
                >
                  {sendingAll ? "Sending…" : `Send All (${pendingCount})`}
                </button>
              )}
            </div>
          </div>
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

      {addingMfrName !== null && (
        <ManufacturerForm
          initial={null}
          prefillManufacturer={addingMfrName}
          onClose={() => setAddingMfrName(null)}
          onSubmit={handleAddManufacturer}
        />
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
  // Encode ALL extractable attachments in the URL (indexed: att_url_0, att_url_1, …)
  // so readContext can reconstruct them on page refresh when sessionStorage is gone.
  const extractables = (ctx.attachments ?? []).filter(isExtractable);
  extractables.forEach((att, i) => {
    qs.set(`att_url_${i}`, att.doc_url);
    qs.set(`att_name_${i}`, att.file_name);
  });
  window.location.hash = `contact-manufacturer?${qs.toString()}`;
}
