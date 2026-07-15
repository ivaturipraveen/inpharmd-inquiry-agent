import { FC, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ClientTools } from "@elevenlabs/react";
import type { ManufacturerContact, ManufacturerContactInput } from "../types";
import VoiceFillButton from "./VoiceFillButton";

interface Props {
  initial?: ManufacturerContact | null;
  prefillManufacturer?: string;
  onClose: () => void;
  onSubmit: (data: ManufacturerContactInput) => Promise<void>;
}

const emptyForm: ManufacturerContactInput = {
  manufacturer: "",
  parent_owner: "",
  preferred_channel: "",
  official_mi_email: "",
  team_verified_email: "",
  email_deliverable: "",
  mi_web_form_url: "",
  mi_phone: "",
  mi_phone_hours: "",
  mi_fax: "",
  hcp_portal_url: "",
  hcp_registration_required: "",
  typical_response_sla: "",
  last_outreach_date: "",
  last_outreach_status: "",
  notes: "",
};

const CHANNELS = ["Web Form", "Email", "Phone", "HCP Portal", "Fax", "Other"];
const YES_NO = ["Yes", "No", "Unknown"];

const ManufacturerForm: FC<Props> = ({ initial, prefillManufacturer, onClose, onSubmit }) => {
  const [form, setForm] = useState<ManufacturerContactInput>(() =>
    prefillManufacturer ? { ...emptyForm, manufacturer: prefillManufacturer } : emptyForm
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    if (initial) {
      setForm({
        manufacturer: initial.manufacturer ?? "",
        parent_owner: initial.parent_owner ?? "",
        preferred_channel: initial.preferred_channel ?? "",
        official_mi_email: initial.official_mi_email ?? "",
        team_verified_email: initial.team_verified_email ?? "",
        email_deliverable: initial.email_deliverable ?? "",
        mi_web_form_url: initial.mi_web_form_url ?? "",
        mi_phone: initial.mi_phone ?? "",
        mi_phone_hours: initial.mi_phone_hours ?? "",
        mi_fax: initial.mi_fax ?? "",
        hcp_portal_url: initial.hcp_portal_url ?? "",
        hcp_registration_required: initial.hcp_registration_required ?? "",
        typical_response_sla: initial.typical_response_sla ?? "",
        last_outreach_date: initial.last_outreach_date ?? "",
        last_outreach_status: initial.last_outreach_status ?? "",
        notes: initial.notes ?? "",
      });
    } else {
      setForm(emptyForm);
    }
  }, [initial]);

  const update =
    (key: keyof ManufacturerContactInput) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [key]: e.target.value }));

  // Map of fields the voice agent may set, with light value normalization.
  const VOICE_FIELDS: Record<
    string,
    { label: string; normalize?: (v: string) => string | null }
  > = useMemo(
    () => ({
      manufacturer: { label: "Manufacturer name" },
      parent_owner: { label: "Parent / owner" },
      preferred_channel: {
        label: "Preferred channel",
        normalize: (v) => {
          const lower = v.toLowerCase();
          const match = CHANNELS.find((c) => c.toLowerCase() === lower);
          return match ?? v;
        },
      },
      official_mi_email: { label: "Official MI email" },
      team_verified_email: { label: "Team-verified email" },
      email_deliverable: {
        label: "Email deliverable",
        normalize: (v) => {
          const m = YES_NO.find((x) => x.toLowerCase() === v.toLowerCase());
          return m ?? v;
        },
      },
      mi_web_form_url: { label: "MI web-form URL" },
      mi_phone: { label: "MI phone" },
      mi_phone_hours: { label: "MI phone hours" },
      mi_fax: { label: "MI fax" },
      hcp_portal_url: { label: "HCP portal URL" },
      hcp_registration_required: {
        label: "HCP registration required",
        normalize: (v) => {
          const m = YES_NO.find((x) => x.toLowerCase() === v.toLowerCase());
          return m ?? v;
        },
      },
      typical_response_sla: { label: "Typical SLA" },
      last_outreach_date: { label: "Last outreach date" },
      last_outreach_status: { label: "Last outreach status" },
      notes: { label: "Notes" },
    }),
    []
  );

  const voiceTools: ClientTools = useMemo(
    () => ({
      set_field: async (params) => {
        const field = String(params?.field ?? "").trim();
        const raw = params?.value;
        const value = raw == null ? "" : String(raw);
        const spec = VOICE_FIELDS[field];
        if (!spec)
          return `Unknown field "${field}". Valid fields: ${Object.keys(
            VOICE_FIELDS
          ).join(", ")}.`;
        const normalized = spec.normalize ? spec.normalize(value) : value;
        setForm((f) => ({ ...f, [field]: normalized }));
        return `${spec.label} set.`;
      },
      submit_form: async () => {
        if (!form.manufacturer?.trim())
          return "Cannot submit — manufacturer name is required.";
        formRef.current?.requestSubmit();
        return "Submitting now.";
      },
    }),
    [VOICE_FIELDS, form.manufacturer]
  );

  const voiceVars = useMemo(
    () => ({
      form_type: "manufacturer",
      mode: initial ? "edit" : "create",
      current_manufacturer: form.manufacturer || "(empty)",
    }),
    [initial, form.manufacturer]
  );

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!form.manufacturer.trim()) {
      setError("Manufacturer name is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const cleaned: ManufacturerContactInput = Object.fromEntries(
        Object.entries(form).map(([k, v]) => [k, v === "" ? null : v])
      ) as ManufacturerContactInput;
      cleaned.manufacturer = form.manufacturer.trim();
      await onSubmit(cleaned);
    } catch (err: any) {
      setError(err?.message ?? "Failed to save.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        // Only close when the mousedown started on the backdrop itself —
        // otherwise dragging from inside an input (e.g. text selection) past
        // the edge would dismiss the dialog mid-edit.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <form ref={formRef} onSubmit={handleSubmit}>
          <div className="modal-header">
            <h2>{initial ? "Edit Manufacturer" : "Add New Manufacturer"}</h2>
            <div className="modal-header-actions">
              <VoiceFillButton
                clientTools={voiceTools}
                dynamicVariables={voiceVars}
              />
              <button
                type="button"
                className="modal-close"
                onClick={onClose}
                aria-label="Close"
              >
                ×
              </button>
            </div>
          </div>

          <div className="modal-body">
            {error && <div className="error-banner">{error}</div>}

            <div className="form-grid">
              <div className="field full">
                <label>
                  Manufacturer<span className="req">*</span>
                </label>
                <input
                  type="text"
                  value={form.manufacturer ?? ""}
                  onChange={update("manufacturer")}
                  required
                  placeholder="e.g. Pfizer"
                />
              </div>

              <div className="field">
                <label>Parent / Owner</label>
                <input
                  type="text"
                  value={form.parent_owner ?? ""}
                  onChange={update("parent_owner")}
                />
              </div>

              <div className="field">
                <label>Preferred Channel</label>
                <select
                  value={form.preferred_channel ?? ""}
                  onChange={update("preferred_channel")}
                >
                  <option value="">—</option>
                  {CHANNELS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label>Official MI Email</label>
                <input
                  type="email"
                  value={form.official_mi_email ?? ""}
                  onChange={update("official_mi_email")}
                />
              </div>

              <div className="field">
                <label>Team-Verified Email</label>
                <input
                  type="text"
                  value={form.team_verified_email ?? ""}
                  onChange={update("team_verified_email")}
                />
              </div>

              <div className="field">
                <label>Email Deliverable?</label>
                <select
                  value={form.email_deliverable ?? ""}
                  onChange={update("email_deliverable")}
                >
                  <option value="">—</option>
                  {YES_NO.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label>MI Phone</label>
                <input
                  type="text"
                  value={form.mi_phone ?? ""}
                  onChange={update("mi_phone")}
                  placeholder="1-800-..."
                />
              </div>

              <div className="field full">
                <label>MI Web Form URL</label>
                <input
                  type="url"
                  value={form.mi_web_form_url ?? ""}
                  onChange={update("mi_web_form_url")}
                  placeholder="https://..."
                />
              </div>

              <div className="field">
                <label>MI Phone Hours</label>
                <input
                  type="text"
                  value={form.mi_phone_hours ?? ""}
                  onChange={update("mi_phone_hours")}
                  placeholder="Mon-Fri 8a-6p ET"
                />
              </div>

              <div className="field">
                <label>MI Fax</label>
                <input
                  type="text"
                  value={form.mi_fax ?? ""}
                  onChange={update("mi_fax")}
                />
              </div>

              <div className="field full">
                <label>HCP Portal / Login URL</label>
                <input
                  type="url"
                  value={form.hcp_portal_url ?? ""}
                  onChange={update("hcp_portal_url")}
                  placeholder="https://..."
                />
              </div>

              <div className="field">
                <label>HCP Registration Required?</label>
                <select
                  value={form.hcp_registration_required ?? ""}
                  onChange={update("hcp_registration_required")}
                >
                  <option value="">—</option>
                  {YES_NO.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </div>

              <div className="field">
                <label>Typical Response SLA</label>
                <input
                  type="text"
                  value={form.typical_response_sla ?? ""}
                  onChange={update("typical_response_sla")}
                  placeholder="5-10 business days"
                />
              </div>

              <div className="field">
                <label>Last Outreach Date</label>
                <input
                  type="date"
                  value={form.last_outreach_date ?? ""}
                  onChange={update("last_outreach_date")}
                />
              </div>

              <div className="field">
                <label>Last Outreach Status</label>
                <input
                  type="text"
                  value={form.last_outreach_status ?? ""}
                  onChange={update("last_outreach_status")}
                />
              </div>

              <div className="field full">
                <label>Notes</label>
                <textarea
                  value={form.notes ?? ""}
                  onChange={update("notes")}
                  rows={3}
                />
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
              {submitting ? "Saving…" : initial ? "Save Changes" : "Submit"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default ManufacturerForm;
