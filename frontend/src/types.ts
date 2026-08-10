/** Must match backend schemas.INQUIRY_SUBJECT_MAX_LENGTH */
export const INQUIRY_SUBJECT_MAX_LENGTH = 1000;

export interface ManufacturerContact {
  id: number;
  manufacturer: string;
  parent_owner?: string | null;
  preferred_channel?: string | null;
  official_mi_email?: string | null;
  team_verified_email?: string | null;
  email_deliverable?: string | null;
  mi_web_form_url?: string | null;
  mi_phone?: string | null;
  mi_phone_hours?: string | null;
  mi_fax?: string | null;
  hcp_portal_url?: string | null;
  hcp_registration_required?: string | null;
  typical_response_sla?: string | null;
  last_outreach_date?: string | null;
  last_outreach_status?: string | null;
  fallback_call_enabled?: boolean | null;
  notes?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export type ManufacturerContactInput = Omit<
  ManufacturerContact,
  "id" | "created_at" | "updated_at"
>;

export type InquiryStatus =
  | "draft"
  | "email_pending"
  | "email_sent"
  | "email_responded"
  | "call_pending"
  | "call_completed"
  | "needs_attention"
  | "closed"
  | "failed";

export interface ManufacturerSummary {
  id: number;
  manufacturer: string;
  official_mi_email?: string | null;
  team_verified_email?: string | null;
  mi_phone?: string | null;
  typical_response_sla?: string | null;
}

export interface InquiryAttachment {
  id: number;
  url: string;
  filename?: string | null;
  content_type?: string | null;
  summary?: string | null;
  display_order?: number;
  reply_id?: number | null;
  created_at?: string | null;
}

export interface EmailReply {
  id: number;
  direction: "inbound" | "outbound";
  sender_email?: string | null;
  body?: string | null;
  sent_at: string;
  attachments: InquiryAttachment[];
}

export interface Inquiry {
  id: number;
  manufacturer_id: number | null;
  subject: string;
  question: string;
  requester_name?: string | null;
  requester_email?: string | null;
  fallback_after_hours: number;
  status: InquiryStatus;
  email_scheduled_for?: string | null;
  email_sent_at?: string | null;
  email_response_at?: string | null;
  email_response?: string | null;
  call_scheduled_for?: string | null;
  call_completed_at?: string | null;
  call_transcript?: string | null;
  call_summary?: string | null;
  call_conversation_id?: string | null;
  call_provider_status?: string | null;
  retry_count?: number;
  max_retries?: number;
  next_retry_at?: string | null;
  is_test_call?: boolean;
  test_call_phone?: string | null;
  final_answer?: string | null;
  pdf_url?: string | null;
  pdf_filename?: string | null;
  pdf_summary?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  manufacturer?: ManufacturerSummary | null;
  medication_name?: string | null;
  pi_storage_data?: string | null;
  // Set when forwarded from an InpharmD platform inquiry (MUE Excel grouping).
  source_inquiry_uuid?: string | null;
  inbound_attachments?: InquiryAttachment[] | null;
  email_replies?: EmailReply[] | null;
  // Populated only in all-users list: display name or email of the creator.
  created_by?: string | null;
}

export interface InquiryInput {
  manufacturer_id: number;
  subject: string;
  question: string;
  requester_name?: string | null;
  requester_email?: string | null;
  fallback_after_hours: number;
  // When forwarded from the InpharmD Inquiries tab, the original platform
  // UUID — used to POST the response back to the legacy endpoint once a
  // manufacturer answers (by email or voice).
  source_inquiry_uuid?: string | null;
}

// Shape emitted by InquiryForm. Carries manufacturer_ids (array) so the
// form supports multi-select. ContactManufacturerPage maps this to either
// InquiryInput (single) or a bulkCreate payload (multiple).
export interface InquiryFormData {
  manufacturer_ids: number[];
  subject: string;
  question: string;
  requester_name?: string | null;
  requester_email?: string | null;
  fallback_after_hours: number;
}
