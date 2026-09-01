import { useCallback, useEffect, useMemo, useState } from "react";
import InquiryForm from "../components/InquiryForm";
import ChannelChooser from "../components/ChannelChooser";
import ManufacturerForm from "../components/ManufacturerForm";
import StatusBadge from "../components/StatusBadge";
import { api } from "../api";
import { isWithinBusinessHoursNow } from "../utils/businessHours";
import { fmtFallbackHours, fmtFallbackStatus, FALLBACK_PRESETS } from "../utils/fallback";
import type {
  Inquiry,
  InquiryInput,
  InquiryFormData,
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
  // From InpharmD's inquiry_submitter_details.team_name, if the platform
  // returned one for this MUE inquiry's submitter.
  team_name?: string;
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

// The backend is the single source of truth for Inquiry.subject — it always
// overwrites it to `Drug information request [InpharmD #<id>]` once the row
// exists. This placeholder is shown pre-creation everywhere; it is never
// sent to the backend as a meaningful value (accepted but discarded).
const PENDING_SUBJECT = "Drug information request [InpharmD #pending]";

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
  const team_name = params.get("team_name") ?? undefined;
  return { uuid, title, attachments, ...(team_name ? { team_name } : {}) };
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
  const [pendingInquiryInput, setPendingInquiryInput] = useState<InquiryInput | null>(null);
  // Tracks the inquiry id created during the deferred-create flow. Prevents a
  // duplicate inquiry if the second step (sendEmail / triggerCall) fails and
  // the user retries — subsequent attempts reuse this id instead of creating again.
  const [pendingCreatedId, setPendingCreatedId] = useState<number | null>(null);
  // Multi-manufacturer manual flow: holds InquiryFormData with manufacturer_ids.length > 1
  const [pendingBulkManualInput, setPendingBulkManualInput] = useState<InquiryFormData | null>(null);
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
  // Per-row fallback-hours override, keyed by the same selKey(attIdx, rowIndex)
  // as selectedKeys. A row with no entry here falls back to the batch-level
  // `fallbackHours` below — same override-with-default semantics the backend
  // already applies to BulkTarget.fallback_after_hours.
  const [rowFallbackHours, setRowFallbackHours] = useState<Record<string, number>>({});
  const [search, setSearch] = useState("");
  const [subject, setSubject] = useState(PENDING_SUBJECT);
  const [question, setQuestion] = useState("");
  // Requesting pharmacist's team/organization — shared across the Excel
  // ("multi") flow's own bulkCreate call and, as defaultTeamName, the
  // single-manufacturer InquiryForm below.
  const [teamName, setTeamName] = useState("");
  const [fallbackHours, setFallbackHours] = useState(24);
  const [submitting, setSubmitting] = useState<BulkChannel | null>(null);
  const [testCountryCode, setTestCountryCode] = useState("+1");
  const [testLocal, setTestLocal] = useState("");


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
    // Subject is always backend-generated — never seeded from ctx.title.
    // The original MUE title still seeds the question/details field.
    if (ctx?.title) {
      setQuestion(ctx.title);
    }
    if (ctx?.team_name) setTeamName(ctx.team_name);
  }, [ctx]);

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 6000);
    return () => clearTimeout(t);
  }, [banner]);

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);

  useEffect(() => {
    if (!extractError) return;
    const t = setTimeout(() => setExtractError(null), 6000);
    return () => clearTimeout(t);
  }, [extractError]);

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

  const loadExistingInquiries = useCallback(() => {
    if (!ctx?.uuid) return;
    api.inquiries
      .list({ source_inquiry_uuid: ctx.uuid, all_users: true })
      .then(setExistingInquiries)
      .catch(() => {});
  }, [ctx?.uuid]);

  useEffect(() => {
    loadExistingInquiries();
  }, [loadExistingInquiries]);

  useEffect(() => {
    let lastRefreshAt = 0;
    const refresh = () => {
      const now = Date.now();
      if (now - lastRefreshAt < 500) return;
      lastRefreshAt = now;
      loadExistingInquiries();
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("focus", refresh);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("focus", refresh);
    };
  }, [loadExistingInquiries]);

  // Map manufacturer_id → inquiry for the first (most-recent) contact per mfr.
  // Test call inquiries have manufacturer_id = null and are excluded from this map.
  const contactedMfrMap = useMemo(() => {
    const m = new Map<number, Inquiry>();
    for (const inq of existingInquiries) {
      if (inq.manufacturer_id != null && !m.has(inq.manufacturer_id))
        m.set(inq.manufacturer_id, inq);
    }
    return m;
  }, [existingInquiries]);

  // Every manufacturer_id claimed by a matched row in any attachment, across
  // all files — used to find already-contacted manufacturers that aren't
  // represented by any row in the current extraction (e.g. contacted via the
  // manual flow, or no longer matching this Excel's text) so they don't
  // silently vanish from "Already contacted" once matching finishes.
  const claimedContactedIds = useMemo(() => {
    const ids = new Set<number>();
    for (const s of attachmentExtractions) {
      s.result?.rows.forEach((r) => {
        if (r.matched_id != null) ids.add(r.matched_id);
      });
    }
    return ids;
  }, [attachmentExtractions]);

  const unclaimedContactedInquiries = useMemo(() => {
    return Array.from(contactedMfrMap.values()).filter(
      (inq) => inq.manufacturer_id != null && !claimedContactedIds.has(inq.manufacturer_id),
    );
  }, [contactedMfrMap, claimedContactedIds]);

  // Single source of truth for the "Already contacted" list — merges rows
  // matched in the current extraction with unclaimed contacted inquiries
  // into ONE deduplicated-by-manufacturer list, so the page never renders
  // two separately-headed "Already contacted" sections for the same MUE,
  // and the same manufacturer can never appear twice even if it happens to
  // match a row in more than one attachment.
  type ContactedDisplayItem = {
    key: string;
    name: string;
    medication?: string | null;
    piStorage?: string | null;
    inq: Inquiry;
  };
  const allContactedDisplayItems = useMemo((): ContactedDisplayItem[] => {
    const byMfrId = new Map<number, ContactedDisplayItem>();
    for (const s of attachmentExtractions) {
      s.result?.rows.forEach((r) => {
        if (r.matched_id != null && !byMfrId.has(r.matched_id)) {
          const inq = contactedMfrMap.get(r.matched_id);
          if (inq) {
            byMfrId.set(r.matched_id, {
              key: `mfr-${r.matched_id}`,
              name: r.matched_name || r.raw_name,
              medication: r.medication_name,
              piStorage: r.pi_storage,
              inq,
            });
          }
        }
      });
    }
    for (const inq of unclaimedContactedInquiries) {
      const mfrId = inq.manufacturer_id as number;
      if (byMfrId.has(mfrId)) continue;
      const mfr = mfrById[mfrId];
      byMfrId.set(mfrId, {
        key: `mfr-${mfrId}`,
        name: mfr?.manufacturer ?? inq.manufacturer?.manufacturer ?? `Manufacturer #${mfrId}`,
        medication: inq.medication_name,
        piStorage: inq.pi_storage_data,
        inq,
      });
    }
    return Array.from(byMfrId.values());
  }, [attachmentExtractions, contactedMfrMap, unclaimedContactedInquiries, mfrById]);

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
    async (data: InquiryFormData) => {
      if (data.targets.length === 1) {
        const t = data.targets[0];
        const payload: InquiryInput = {
          manufacturer_id: t.manufacturer_id,
          subject: data.subject,
          question: data.question,
          requester_name: data.requester_name,
          requester_email: data.requester_email,
          fallback_after_hours: t.fallback_after_hours,
          medication_name: t.medication_name,
          team_name: data.team_name,
          ...(ctx?.uuid ? { source_inquiry_uuid: ctx.uuid } : {}),
        };
        setPendingInquiryInput(payload);
      } else {
        setPendingBulkManualInput(data);
      }
    },
    [ctx],
  );

  // Stable reference — used as onClose in ChannelChooser and as cleanup after
  // every successful action. Also referenced in the Escape useEffect dep array,
  // so a new function reference on every render would tear down and re-add the
  // listener on each parent re-render while the modal is open.
  const closePending = useCallback(() => {
    setPendingInquiryInput(null);
    setPendingCreatedId(null);
  }, []);

  const closePendingBulk = useCallback(() => {
    setPendingBulkManualInput(null);
  }, []);

  const handleAddManufacturer = useCallback(async (data: ManufacturerContactInput) => {
    await api.manufacturers.create(data);
    setAddingMfrName(null);
    reloadManufacturers();
    // Re-run extraction so the newly added manufacturer appears matched.
    setAttachmentExtractions([]);
    setSelectedKeys(new Set());
    setRowFallbackHours({});
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
      manualOverride
    ) return;
    runExtractions();
  }, [extractableAttachments, attachmentExtractions, extractError, manualOverride, runExtractions]);


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


  const handleBulkSubmit = async (channel: BulkChannel) => {
    if (attachmentExtractions.length === 0 || !ctx) return;
    if (!question.trim()) {
      setExtractError("Question is required.");
      return;
    }

    // Test call is entirely separate from the bulk dispatch flow —
    // no manufacturers are selected, no inquiries are created.
    if (channel === "test_call") {
      const digits = digitsOnly(testLocal);
      if (digits.length < 7) {
        setExtractError("Enter a valid phone number for the test call (at least 7 digits).");
        return;
      }
      const phoneNumber = `${testCountryCode}${digits}`;
      // Match the entered number against known manufacturer phones (exact match).
      const matchedMfr = manufacturers.find(
        (m) => m.mi_phone && digitsOnly(m.mi_phone) === digits
      );
      setSubmitting("test_call");
      setExtractError(null);
      try {
        await api.inquiries.testCallPreview({
          phone_number: phoneNumber,
          subject: subject.trim(),
          question: question.trim(),
          manufacturer_id: matchedMfr?.id ?? null,
        });
        setBanner(
          matchedMfr
            ? `Test call dialing ${phoneNumber} with ${matchedMfr.manufacturer} context.`
            : `Test call dialing ${phoneNumber}.`
        );
      } catch (e: any) {
        setExtractError(e?.message ?? "Test call failed.");
      } finally {
        setSubmitting(null);
      }
      return;
    }

    if (selectedKeys.size === 0) {
      setExtractError("Pick at least one manufacturer.");
      return;
    }

    setSubmitting(channel);
    setExtractError(null);

    try {
      // Group selected rows by source file and make one bulkCreate per file
      // so each inquiry gets the correct source_excel_url for response writeback.
      const byFile: Array<{
        s: AttachmentExtractionState;
        targets: { manufacturer_id: number; source_excel_row: number; medication_name: string | null; pi_storage_data: string | null; pi_link: string | null; fallback_after_hours: number }[];
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
            // Per-row override when the user picked one; otherwise the
            // batch-level fallbackHours applies (same default the backend
            // already falls back to when this field is omitted).
            fallback_after_hours: rowFallbackHours[selKey(attIdx, r.row_index)] ?? fallbackHours,
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

      for (let fileIdx = 0; fileIdx < byFile.length; fileIdx++) {
        const { s, targets } = byFile[fileIdx];
        const result = await api.inquiries.bulkCreate({
          targets,
          subject: subject.trim(),
          question: question.trim(),
          fallback_after_hours: fallbackHours,
          team_name: teamName.trim() || null,
          source_inquiry_uuid: ctx.uuid,
          source_excel_url: s.result!.excel_s3_url ?? s.att.doc_url ?? null,
          source_excel_sheet: s.result!.sheet_name,
          dispatch_channel: channel,
        });
        allCreated.push(...result.created);
        allFailed.push(...result.failed);
        totalDispatched += result.dispatched ?? 0;
      }

      if (channel === "call") {
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

      loadExistingInquiries();
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
      {extractableAttachments.length > 0 && (anyExtracting || anyExtracted) &&
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
                    setRowFallbackHours({});
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

      {/* Already Contacted — shown when there are no attachment extractions to render the section inline */}
      {!attachmentExtractions.some((s) => s.result) && contactedMfrMap.size > 0 && (
        <div className="page-form">
          <div className="page-form-body">
            <div className="contacted-section">
              <div className="contacted-section-header">
                Already contacted ({existingInquiries.length})
              </div>
              {existingInquiries.map((inq) => {
                const mfr = inq.manufacturer_id != null ? mfrById[inq.manufacturer_id] : undefined;
                const isScheduled = inq.status === "email_pending" && !!inq.email_scheduled_for;
                return (
                  <div key={inq.id} className="contacted-row">
                    <div className="contacted-row-main">
                      <span className="contacted-row-name">
                        {mfr?.manufacturer ?? inq.manufacturer?.manufacturer ?? (inq.test_call_phone ? `Test Call — ${inq.test_call_phone}` : `Manufacturer #${inq.manufacturer_id}`)}
                      </span>
                      {(inq.medication_name || inq.pi_storage_data) && (
                        <div className="bulk-row-product-info" style={{ marginTop: 2 }}>
                          {inq.medication_name && (
                            <span className="bulk-row-product-pill">💊 {inq.medication_name}</span>
                          )}
                          {inq.pi_storage_data && (
                            <span className="bulk-row-product-pill">🌡 {inq.pi_storage_data}</span>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="contacted-row-status" style={{ alignItems: "flex-start" }}>
                      {isScheduled ? (
                        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
                          <span className="pill pill-green">Scheduled</span>
                          <span className="cell-muted" style={{ fontSize: "0.72rem" }}>
                            {new Date(inq.email_scheduled_for!).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                          </span>
                        </div>
                      ) : (
                        <StatusBadge status={inq.status} />
                      )}
                      <span className="contacted-row-id">#{inq.id}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* MULTI mode: manufacturer lists grouped by file */}
      {mode === "multi" && anyExtracted && (() => {
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
                                      {matched && (() => {
                                        const eligible = !!(mfr?.fallback_call_enabled && mfr?.mi_phone);
                                        const effectiveHours = rowFallbackHours[key] ?? fallbackHours;
                                        return (
                                          <div className="bulk-row-mfr-meta" style={{ marginTop: 4 }}>
                                            {eligible ? (
                                              <span>
                                                <span className="cell-muted">Fallback call after</span>
                                                <select
                                                  className="filter-select mfr-target-fallback-select"
                                                  value={effectiveHours}
                                                  onClick={(e) => e.stopPropagation()}
                                                  onChange={(e) => {
                                                    const hours = Number(e.target.value);
                                                    setRowFallbackHours((prev) => ({ ...prev, [key]: hours }));
                                                  }}
                                                >
                                                  {FALLBACK_PRESETS.map((p) => (
                                                    <option key={p.hours} value={p.hours}>{p.label}</option>
                                                  ))}
                                                </select>
                                              </span>
                                            ) : (
                                              <span className="cell-muted">
                                                {fmtFallbackStatus(mfr?.fallback_call_enabled, effectiveHours, mfr?.mi_phone)}
                                              </span>
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
                          </>
                        );
                      })()}
                    </div>
                  );
                })}

                {/* Single "Already contacted" section for the whole MUE —
                    merges rows matched in this extraction with contacted
                    manufacturers not represented by any row (manual flow,
                    or no longer textually matching this Excel), deduplicated
                    by manufacturer so nothing shows twice and nothing
                    silently vanishes once matching completes. */}
                {allContactedDisplayItems.length > 0 && (
                  <div className="contacted-section" style={{ marginTop: attachmentExtractions.some(x => x.result) ? "24px" : "0" }}>
                    <div className="contacted-section-header">
                      Already contacted ({allContactedDisplayItems.length})
                    </div>
                    {allContactedDisplayItems.map((item) => {
                      const inq = item.inq;
                      const isScheduled = inq.status === "email_pending" && !!inq.email_scheduled_for;
                      return (
                        <div key={item.key} className="contacted-row">
                          <div className="contacted-row-main">
                            <span className="contacted-row-name">{item.name}</span>
                            {(item.medication || item.piStorage) && (
                              <div className="bulk-row-product-info" style={{ marginTop: 2 }}>
                                {item.medication && (
                                  <span className="bulk-row-product-pill">💊 {item.medication}</span>
                                )}
                                {item.piStorage && (
                                  <span className="bulk-row-product-pill">🌡 {item.piStorage}</span>
                                )}
                              </div>
                            )}
                          </div>
                          <div className="contacted-row-status" style={{ alignItems: "flex-start" }}>
                            {isScheduled ? (
                              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
                                <span className="pill pill-green">Scheduled</span>
                                <span className="cell-muted" style={{ fontSize: "0.72rem" }}>
                                  {new Date(inq.email_scheduled_for!).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                                </span>
                              </div>
                            ) : (
                              <StatusBadge status={inq.status} />
                            )}
                            <span className="contacted-row-id">#{inq.id}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
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
                    <label>Question / Details<span className="req">*</span></label>
                    <textarea
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      rows={5}
                    />
                  </div>
                  <div className="field full">
                    <label>Team Name <span className="label-hint">optional</span></label>
                    <input
                      type="text"
                      value={teamName}
                      onChange={(e) => setTeamName(e.target.value)}
                      placeholder="e.g. MedStar Health — shown in the outbound email"
                    />
                  </div>
                  <div className="field full">
                    <label>If no email response within</label>
                    <select
                      className="filter-select"
                      value={fallbackHours}
                      onChange={(e) => setFallbackHours(Number(e.target.value))}
                    >
                      {FALLBACK_PRESETS.map((p) => (
                        <option key={p.hours} value={p.hours}>{p.label}</option>
                      ))}
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
              const fallbackEligibleCount = allSelectedRows.filter((r) => {
                const m = r.matched_id ? mfrById[r.matched_id] : undefined;
                return !!(m?.fallback_call_enabled && m?.mi_phone);
              }).length;
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
                          {fallbackEligibleCount > 0 && (
                            <>
                              {" "}Voice agent will call{" "}
                              {fallbackEligibleCount < total
                                ? <>{fallbackEligibleCount} of {total} that don't</>
                                : <>any that don't</>}{" "}
                              reply within{" "}
                              <strong>{fmtFallbackHours(fallbackHours)}</strong>.
                            </>
                          )}
                        </div>
                        <ul className="channel-meta">
                          <li>
                            <span>Reachable</span> {reachableByEmail} of {total} have email
                          </li>
                          {fallbackEligibleCount > 0 && (
                            <li>
                              <span>Fallback</span> agent call after {fmtFallbackHours(fallbackHours)}
                              {fallbackEligibleCount < total && (
                                <span className="cell-muted"> · {fallbackEligibleCount} of {total} eligible</span>
                              )}
                            </li>
                          )}
                        </ul>
                        <button
                          className="btn btn-primary"
                          type="button"
                          disabled={noneSelected || reachableByEmail === 0 || anyBusy}
                          onClick={() => handleBulkSubmit("email")}
                        >
                          {submitting === "email" ? "Sending…" : `Send Email (${reachableByEmail || total})`}
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
                          <strong>no manufacturer is called</strong>. The
                          transcript is saved in Outreach when the call ends.
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


      {/* SINGLE mode: existing form */}
      {mode === "single" && (
        loadingMfrs ? (
          <div className="empty">
            <div className="empty-title">Loading manufacturers…</div>
          </div>
        ) : (
          <InquiryForm
            manufacturers={manufacturers}
            defaultSubject={subject}
            defaultQuestion={question}
            defaultTeamName={teamName}
            variant="page"
            title="Contact Manufacturer"
            submitLabel="Create & choose channel"
            onClose={handleCancel}
            onSubmit={handleSingleCreate}
          />
        )
      )}


      {pendingInquiryInput && (() => {
        const mfr = pendingInquiryInput.manufacturer_id != null
          ? mfrById[pendingInquiryInput.manufacturer_id]
          : undefined;

        // Idempotent create: if a previous attempt already created the inquiry
        // (pendingCreatedId is set) reuse that id instead of creating again.
        // This prevents duplicate drafts when sendEmail / triggerCall fails and
        // the user retries from within the same modal session.
        const getOrCreateId = async (): Promise<number> => {
          if (pendingCreatedId != null) return pendingCreatedId;
          const created = await api.inquiries.create(pendingInquiryInput);
          setPendingCreatedId(created.id);
          return created.id;
        };

        return (
          <ChannelChooser
            manufacturers={mfr ? [mfr] : []}
            fallbackHours={pendingInquiryInput.fallback_after_hours}
            onClose={closePending}
            onSendEmail={async () => {
              const id = await getOrCreateId();
              await api.inquiries.sendEmail(id);
              setBanner(`Email queued for ${mfr?.manufacturer ?? "manufacturer"}.`);
              closePending();
              goTo("inquiries");
            }}
            onCallAgent={async () => {
              const id = await getOrCreateId();
              await api.inquiries.triggerCall(id);
              setBanner("Call placed — the agent is dialing now.");
              closePending();
              goTo("inquiries");
            }}
            onSaveDraft={async () => {
              await getOrCreateId();
              setBanner("Inquiry saved as draft.");
              closePending();
              goTo("inquiries");
            }}
          />
        );
      })()}

      {pendingBulkManualInput && (() => {
        const mfrs = pendingBulkManualInput.targets
          .map(t => mfrById[t.manufacturer_id])
          .filter((x): x is ManufacturerContact => x != null);

        // Only manufacturers where a fallback call could actually happen are
        // "applicable" for this comparison — matches InquiryForm's own
        // fallback-eligibility check (fallback_call_enabled && mi_phone).
        const eligibleFallbackHours = pendingBulkManualInput.targets
          .filter(t => {
            const mfr = mfrById[t.manufacturer_id];
            return !!mfr?.fallback_call_enabled && !!mfr?.mi_phone;
          })
          .map(t => t.fallback_after_hours);
        const fallbackHoursVaries = new Set(eligibleFallbackHours).size > 1;

        const bulkDispatch = async (channel: "email" | "call" | "none") => {
          const result = await api.inquiries.bulkCreate({
            // Each target already carries its own medication_name and
            // fallback_after_hours — passed straight through, no remapping.
            targets: pendingBulkManualInput.targets,
            subject: pendingBulkManualInput.subject,
            question: pendingBulkManualInput.question,
            // Batch-level default only; every target above supplies its own
            // explicit value, so this is effectively unused here.
            fallback_after_hours: pendingBulkManualInput.targets[0]?.fallback_after_hours ?? 24,
            team_name: pendingBulkManualInput.team_name ?? null,
            source_inquiry_uuid: ctx.uuid ?? null,
            source_excel_url: null,
            source_excel_sheet: null,
            dispatch_channel: channel,
          });
          const total = result.created.length;
          const failed = result.failed.length;
          if (channel === "call") {
            setBanner(
              `Calling ${result.dispatched ?? 0} manufacturer${(result.dispatched ?? 0) === 1 ? "" : "s"}` +
                (failed > 0 ? ` · ${failed} skipped` : ""),
            );
          } else if (channel === "email") {
            setBanner(
              `Emailed ${total} manufacturer${total === 1 ? "" : "s"}` +
                (failed > 0 ? ` · ${failed} failed` : ""),
            );
          } else {
            setBanner(`${total} ${total === 1 ? "inquiry" : "inquiries"} saved as draft.`);
          }
          closePendingBulk();
          goTo("inquiries");
        };

        return (
          <ChannelChooser
            manufacturers={mfrs}
            fallbackHours={pendingBulkManualInput.targets[0]?.fallback_after_hours ?? 24}
            fallbackHoursVaries={fallbackHoursVaries}
            onClose={closePendingBulk}
            onSendEmail={() => bulkDispatch("email")}
            onCallAgent={() => bulkDispatch("call")}
            onSaveDraft={() => bulkDispatch("none")}
          />
        );
      })()}

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
  if (ctx.team_name) qs.set("team_name", ctx.team_name);
  // Encode ALL extractable attachments in the URL (indexed: att_url_0, att_url_1, …)
  // so readContext can reconstruct them on page refresh when sessionStorage is gone.
  const extractables = (ctx.attachments ?? []).filter(isExtractable);
  extractables.forEach((att, i) => {
    qs.set(`att_url_${i}`, att.doc_url);
    qs.set(`att_name_${i}`, att.file_name);
  });
  window.location.hash = `contact-manufacturer?${qs.toString()}`;
}
