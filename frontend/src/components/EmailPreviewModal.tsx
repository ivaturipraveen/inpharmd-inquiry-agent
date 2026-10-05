import { FC, useEffect, useState } from "react";

export interface EmailOverride {
  subject: string;
  body: string;
}

interface Props {
  manufacturerName: string;
  /** Previously-applied override for this exact target this session, if
   * any — shown instead of a fresh fetch so a reopened modal never silently
   * discards an edit the user already made. */
  initialOverride?: EmailOverride | null;
  fetchPreview: () => Promise<EmailOverride>;
  onApply: (override: EmailOverride) => void;
  onClose: () => void;
}

/** Pre-creation "Preview / Edit Email" for the Contact Manufacturer page —
 * same visual pattern as the existing post-creation editor in
 * InquiryDetail.tsx (Subject input + monospace body textarea), adapted so
 * edits are applied to local flow state instead of PATCHed to a real
 * Inquiry (none exists yet at this point). */
const EmailPreviewModal: FC<Props> = ({
  manufacturerName,
  initialOverride,
  fetchPreview,
  onApply,
  onClose,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [hasFetched, setHasFetched] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    fetchPreview()
      .then((result) => {
        setSubject(result.subject);
        setBody(result.body);
        setHasFetched(true);
      })
      .catch((e: any) => setError(e?.message ?? "Failed to compose email preview."))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (initialOverride) {
      setSubject(initialOverride.subject);
      setBody(initialOverride.body);
      setHasFetched(true);
      return;
    }
    load();
    // Only ever runs once per mount — the modal is remounted per-open by its parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Preview / Edit Email — {manufacturerName}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div className="error-banner" style={{ marginBottom: 12 }}>
              {error}{" "}
              <button type="button" className="btn btn-ghost" onClick={load} style={{ marginLeft: 8 }}>
                Retry
              </button>
            </div>
          )}

          {loading && !hasFetched ? (
            <div className="cell-muted">Composing preview…</div>
          ) : (
            hasFetched && (
              <div className="detail-section" style={{ display: "flex", flexDirection: "column" }}>
                <label className="detail-label">Subject</label>
                <input
                  type="text"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  maxLength={1000}
                  style={{ width: "100%" }}
                />
                <label className="detail-label" style={{ marginTop: 8 }}>Email body</label>
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={16}
                  style={{ width: "100%", fontFamily: "monospace" }}
                />
              </div>
            )
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          {initialOverride && (
            <button type="button" className="btn btn-ghost" disabled={loading} onClick={load}>
              Reset to auto-generated
            </button>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={loading || !hasFetched}
            onClick={() => {
              onApply({ subject, body });
              onClose();
            }}
          >
            Use this text
          </button>
        </div>
      </div>
    </div>
  );
};

export default EmailPreviewModal;
