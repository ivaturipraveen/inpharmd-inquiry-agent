from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Email subjects are capped in practice; MUE templates can be long.
INQUIRY_SUBJECT_MAX_LENGTH = 1000


# ---------- Manufacturer ----------
class ManufacturerContactBase(BaseModel):
    manufacturer: str
    parent_owner: Optional[str] = None
    preferred_channel: Optional[str] = None
    official_mi_email: Optional[str] = None
    team_verified_email: Optional[str] = None
    email_deliverable: Optional[str] = None
    mi_web_form_url: Optional[str] = None
    mi_phone: Optional[str] = None
    mi_phone_hours: Optional[str] = None
    mi_fax: Optional[str] = None
    hcp_portal_url: Optional[str] = None
    hcp_registration_required: Optional[str] = None
    typical_response_sla: Optional[str] = None
    last_outreach_date: Optional[date] = None
    last_outreach_status: Optional[str] = None
    fallback_call_enabled: bool = False
    notes: Optional[str] = None


class ManufacturerContactCreate(ManufacturerContactBase):
    pass


class ManufacturerContactUpdate(ManufacturerContactBase):
    manufacturer: Optional[str] = None


class ManufacturerContactOut(ManufacturerContactBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# ---------- Inquiry ----------
InquiryStatus = Literal[
    "draft",
    "email_pending",
    "email_sent",
    "email_responded",
    "call_pending",
    "call_completed",
    "needs_attention",
    "closed",
    "failed",
]


class ManufacturerSummary(BaseModel):
    id: int
    manufacturer: str
    preferred_channel: Optional[str] = None
    official_mi_email: Optional[str] = None
    team_verified_email: Optional[str] = None
    mi_phone: Optional[str] = None
    mi_web_form_url: Optional[str] = None
    typical_response_sla: Optional[str] = None
    fallback_call_enabled: bool = False
    model_config = ConfigDict(from_attributes=True)


class InquiryAttachmentOut(BaseModel):
    id: int
    url: str
    filename: Optional[str] = None
    content_type: Optional[str] = None
    summary: Optional[str] = None
    display_order: int = 0
    reply_id: Optional[int] = None
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class EmailReplyOut(BaseModel):
    id: int
    direction: str
    sender_email: Optional[str] = None
    body: Optional[str] = None
    sent_at: datetime
    attachments: list[InquiryAttachmentOut] = []
    model_config = ConfigDict(from_attributes=True)


class CallLogOut(BaseModel):
    id: int
    conversation_id: Optional[str] = None
    is_test_call: bool = False
    started_at: datetime
    completed_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    provider_status: Optional[str] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class InquiryBase(BaseModel):
    manufacturer_id: Optional[int] = None
    subject: str = Field(..., max_length=INQUIRY_SUBJECT_MAX_LENGTH)
    question: str
    requester_name: Optional[str] = None
    requester_email: Optional[str] = None
    fallback_after_hours: int = 24
    # Set when this inquiry was forwarded from an InpharmD platform inquiry.
    # We POST the manufacturer's response back to the legacy endpoint using
    # this uuid once a final answer is captured.
    source_inquiry_uuid: Optional[str] = None
    # Set when forwarded from a MUE inquiry with an Excel attachment, so the
    # response writeback can find the right row to update.
    source_excel_url: Optional[str] = None
    source_excel_sheet: Optional[str] = None
    source_excel_row: Optional[int] = None
    medication_name: Optional[str] = None
    # Requesting pharmacist's team/organization — from InpharmD's
    # inquiry_submitter_details.team_name, or typed in manually.
    team_name: Optional[str] = None


class InquiryCreate(InquiryBase):
    pass


# ---------- Bulk dispatch (multiple manufacturers, one query) ----------
class BulkTarget(BaseModel):
    manufacturer_id: int
    # Optional per-target row index in the source Excel.
    source_excel_row: Optional[int] = None
    # Product details extracted from the MUE Excel for this row.
    medication_name: Optional[str] = None
    pi_storage_data: Optional[str] = None
    # Per-target override of the batch-level fallback_after_hours below.
    # None (the default) means "use the batch-level value" — this keeps the
    # Excel/MUE bulk flow (which never sets this) unchanged.
    fallback_after_hours: Optional[int] = None
    # DailyMed-enriched fields (populated by the extract-manufacturers endpoint).
    pi_link: Optional[str] = None


class BulkInquiryCreate(BaseModel):
    targets: list[BulkTarget]
    subject: str = Field(..., max_length=INQUIRY_SUBJECT_MAX_LENGTH)
    question: str
    requester_name: Optional[str] = None
    requester_email: Optional[str] = None
    fallback_after_hours: int = 24
    source_inquiry_uuid: Optional[str] = None
    source_excel_url: Optional[str] = None
    source_excel_sheet: Optional[str] = None
    # Same requesting team for every manufacturer in the batch (not per-target).
    team_name: Optional[str] = None
    # Dispatch channel applied to every created inquiry:
    #   "email" — send email to each manufacturer (default)
    #   "call"  — place a voice-agent call to each manufacturer
    #   "none"  — create as drafts only; user dispatches later from Outreach
    dispatch_channel: str = "email"
    # Legacy alias kept so the previous send_email=True payload still works.
    send_email: Optional[bool] = None


class BulkInquiryResult(BaseModel):
    created: list["InquiryOut"]
    failed: list[dict]  # [{ manufacturer_id, error }]
    # Optional summary of what was dispatched. Only present when
    # dispatch_channel != "none".
    dispatch_channel: Optional[str] = None
    dispatched: int = 0


class InquiryUpdate(BaseModel):
    subject: Optional[str] = Field(None, max_length=INQUIRY_SUBJECT_MAX_LENGTH)
    question: Optional[str] = None
    requester_name: Optional[str] = None
    requester_email: Optional[str] = None
    fallback_after_hours: Optional[int] = None
    medication_name: Optional[str] = None
    team_name: Optional[str] = None
    status: Optional[InquiryStatus] = None
    email_response: Optional[str] = None
    call_transcript: Optional[str] = None
    call_summary: Optional[str] = None
    final_answer: Optional[str] = None


class ScheduledEmailContentUpdate(BaseModel):
    subject: str = Field(..., max_length=INQUIRY_SUBJECT_MAX_LENGTH)
    question: str


class EmailResponsePayload(BaseModel):
    response: str


class FollowupEmailPayload(BaseModel):
    body: str = Field(..., min_length=1)


class CallResultPayload(BaseModel):
    transcript: Optional[str] = None
    summary: Optional[str] = None


class TestCallPreviewPayload(BaseModel):
    phone_number: str
    subject: str
    question: str
    manufacturer_id: Optional[int] = None


class InquiryOut(InquiryBase):
    id: int
    user_id: Optional[int] = None
    created_by: Optional[str] = None   # display_name or email of the creating user
    status: InquiryStatus
    email_scheduled_for: Optional[datetime] = None
    email_sent_at: Optional[datetime] = None
    email_message_id: Optional[str] = None
    email_response_at: Optional[datetime] = None
    email_response: Optional[str] = None
    call_scheduled_for: Optional[datetime] = None
    call_completed_at: Optional[datetime] = None
    call_transcript: Optional[str] = None
    call_summary: Optional[str] = None
    call_conversation_id: Optional[str] = None
    call_provider_status: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2
    next_retry_at: Optional[datetime] = None
    is_test_call: bool = False
    test_call_phone: Optional[str] = None
    final_answer: Optional[str] = None
    pdf_url: Optional[str] = None
    pdf_filename: Optional[str] = None
    pdf_summary: Optional[str] = None
    inbound_attachments: list[InquiryAttachmentOut] = []
    email_replies: list[EmailReplyOut] = []
    call_logs: list[CallLogOut] = []
    legacy_response_posted_at: Optional[datetime] = None
    excel_response_url: Optional[str] = None
    excel_response_posted_at: Optional[datetime] = None
    pi_storage_data: Optional[str] = None
    pi_link: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    manufacturer: Optional[ManufacturerSummary] = None

    model_config = ConfigDict(from_attributes=True)


# Resolve the forward reference (BulkInquiryResult → InquiryOut) now that
# InquiryOut is defined.
BulkInquiryResult.model_rebuild()
