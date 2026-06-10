from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


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
    official_mi_email: Optional[str] = None
    team_verified_email: Optional[str] = None
    mi_phone: Optional[str] = None
    typical_response_sla: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class InquiryBase(BaseModel):
    manufacturer_id: int
    subject: str
    question: str
    requester_name: Optional[str] = None
    requester_email: Optional[str] = None
    fallback_after_hours: int = 24
    # Set when this inquiry was forwarded from an InpharmD platform inquiry.
    # We POST the manufacturer's response back to the legacy endpoint using
    # this uuid once a final answer is captured.
    source_inquiry_uuid: Optional[str] = None


class InquiryCreate(InquiryBase):
    pass


class InquiryUpdate(BaseModel):
    subject: Optional[str] = None
    question: Optional[str] = None
    requester_name: Optional[str] = None
    requester_email: Optional[str] = None
    fallback_after_hours: Optional[int] = None
    status: Optional[InquiryStatus] = None
    email_response: Optional[str] = None
    call_transcript: Optional[str] = None
    call_summary: Optional[str] = None
    final_answer: Optional[str] = None


class EmailResponsePayload(BaseModel):
    response: str


class CallResultPayload(BaseModel):
    transcript: Optional[str] = None
    summary: Optional[str] = None


class InquiryOut(InquiryBase):
    id: int
    status: InquiryStatus
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
    final_answer: Optional[str] = None
    pdf_url: Optional[str] = None
    pdf_filename: Optional[str] = None
    pdf_summary: Optional[str] = None
    legacy_response_posted_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    manufacturer: Optional[ManufacturerSummary] = None

    model_config = ConfigDict(from_attributes=True)
