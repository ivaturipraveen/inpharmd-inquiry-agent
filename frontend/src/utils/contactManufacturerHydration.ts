import { api } from "../api";
import { submitterDisplay, typeLabel } from "../pages/ExternalInquiriesPage";

export interface Attachment {
  id: number;
  file_name: string;
  doc_url: string;
}

export interface ForwardContext {
  uuid: string;
  title: string;
  submitter?: string;
  type?: string;
  attachments?: Attachment[];
  // From InpharmD's inquiry_submitter_details.team_name, if the platform
  // returned one for this MUE inquiry's submitter.
  team_name?: string;
  // Raw "Temperature Excursion Request" text from InpharmD (API field
  // `mue_details`), distinct from `title`. TE-only in practice.
  mue_details?: string;
}

// Maps a hydrated list row to ForwardContext, reusing ExternalInquiriesPage's
// own submitterDisplay/typeLabel so values match that flow exactly.
export const mapHydratedContext = (uuid: string, raw: any): ForwardContext => {
  const row = raw && typeof raw === "object" ? raw : {};
  if (row.inquiry_uuid || row.title) {
    const det = row.inquiry_submitter_details ?? {};
    return {
      uuid,
      title: String(row.title ?? "").trim(),
      submitter: submitterDisplay(row),
      type: typeLabel(row),
      attachments: row.attachments ?? undefined,
      team_name: det.team_name ?? undefined,
      mue_details: row.mue_details ?? undefined,
    };
  }
  const a = row.attributes ?? {};
  const det = a["submitter-details"] ?? {};
  return {
    uuid,
    title: String(a.title ?? a.question ?? "").trim(),
    submitter: a.submitter ?? a["submitter-email"] ?? det.email ?? undefined,
    attachments: a.attachments ?? a["all-documents"] ?? undefined,
    team_name: det.team_name ?? undefined,
    mue_details: a.mue_details ?? undefined,
  };
};

const findRow = (data: any, uuid: string): any => {
  const rows: any[] = Array.isArray(data) ? data : Array.isArray(data?.data) ? data.data : [];
  return rows.find((r: any) => r?.inquiry_uuid === uuid);
};

// Resolves a uuid-only deep link to a full ForwardContext via the external-
// inquiries list+search endpoint. The first attempt uses the normal (cached)
// list so the common case stays cheap; a newly-created inquiry can miss that
// cache for up to INPHARMD_LIST_TTL_SECONDS, so a single miss triggers exactly
// one retry with fresh=true before concluding the uuid genuinely isn't there.
export async function fetchHydratedContext(uuid: string): Promise<ForwardContext> {
  const { data } = await api.externalInquiries.list({ search: uuid });
  let row = findRow(data, uuid);
  if (!row) {
    const retry = await api.externalInquiries.list({ search: uuid, fresh: true });
    row = findRow(retry.data, uuid);
  }
  if (!row) {
    throw new Error(`Inquiry ${uuid} was not found in InpharmD (it may no longer be open).`);
  }
  return mapHydratedContext(uuid, row);
}
