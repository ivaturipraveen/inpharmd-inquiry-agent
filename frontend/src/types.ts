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

export interface Inquiry {
  id: number;
  manufacturer_id: number;
  subject: string;
  question: string;
  requester_name?: string | null;
  requester_email?: string | null;
  fallback_after_hours: number;
  status: InquiryStatus;
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
  final_answer?: string | null;
  pdf_url?: string | null;
  pdf_filename?: string | null;
  pdf_summary?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  manufacturer?: ManufacturerSummary | null;
}

export interface InquiryInput {
  manufacturer_id: number;
  subject: string;
  question: string;
  requester_name?: string | null;
  requester_email?: string | null;
  fallback_after_hours: number;
}
