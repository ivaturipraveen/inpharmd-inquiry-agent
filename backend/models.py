from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime

from database import Base


class ManufacturerContact(Base):
    __tablename__ = "manufacturer_contacts"

    id = Column(Integer, primary_key=True, index=True)
    manufacturer = Column(String(255), nullable=False, index=True)
    parent_owner = Column(String(500))
    preferred_channel = Column(String(100))
    official_mi_email = Column(String(255))
    team_verified_email = Column(String(500))
    email_deliverable = Column(String(50))
    mi_web_form_url = Column(Text)
    mi_phone = Column(String(100))
    mi_phone_hours = Column(String(255))
    mi_fax = Column(String(100))
    hcp_portal_url = Column(Text)
    hcp_registration_required = Column(String(50))
    typical_response_sla = Column(String(255))
    last_outreach_date = Column(Date)
    last_outreach_status = Column(String(255))
    notes = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    inquiries = relationship("Inquiry", back_populates="manufacturer", cascade="all, delete-orphan")


# Inquiry lifecycle:
#   draft           -> created, not yet sent
#   email_sent      -> email delivered to manufacturer, awaiting response
#   email_responded -> manufacturer replied (response stored)
#   call_pending    -> SLA elapsed without response, agent call queued
#   call_completed  -> agent call done, transcript / summary stored
#   closed          -> inquiry resolved
#   failed          -> manual mark of unrecoverable failure
INQUIRY_STATUSES = (
    "draft",
    "email_sent",
    "email_responded",
    "call_pending",
    "call_completed",
    "needs_attention",   # call(s) failed; awaiting human follow-up
    "closed",
    "failed",
)


class Inquiry(Base):
    __tablename__ = "inquiries"

    id = Column(Integer, primary_key=True, index=True)
    # Owner — the InpharmD user who created this outreach. The Outreach tab
    # filters by this so each user only sees their own outreach inquiries.
    # Nullable so legacy rows (created before this column existed) still load,
    # but the API treats unowned rows as invisible.
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    manufacturer_id = Column(
        Integer,
        ForeignKey("manufacturer_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subject = Column(String(1000), nullable=False)
    question = Column(Text, nullable=False)
    requester_name = Column(String(255))
    requester_email = Column(String(255))

    # Wait this many hours after the email is sent before falling back to a call.
    fallback_after_hours = Column(Integer, nullable=False, default=24)

    status = Column(String(32), nullable=False, default="draft", index=True)

    email_sent_at = Column(DateTime(timezone=True))
    email_message_id = Column(String(255))
    email_response_at = Column(DateTime(timezone=True))
    email_response = Column(Text)

    call_scheduled_for = Column(DateTime(timezone=True))
    call_completed_at = Column(DateTime(timezone=True))
    call_transcript = Column(Text)
    call_summary = Column(Text)
    call_conversation_id = Column(String(128), index=True)
    call_provider_status = Column(String(64))

    # Auto-retry on voicemail/no_answer
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=2)
    next_retry_at = Column(DateTime(timezone=True), index=True)

    final_answer = Column(Text)

    # PDF attachment that came back with an email reply.
    # url = presigned/public link to the file in object storage (S3, R2, etc.)
    pdf_url = Column(Text)
    pdf_filename = Column(String(512))
    pdf_summary = Column(Text)

    # When this inquiry was forwarded FROM the InpharmD platform (via the
    # "Contact manufacturer" action on the Inpharmd Inquiries tab), we store
    # the original staging-side UUID here so we can POST the manufacturer's
    # response back to the legacy /api/legacy/manufacturing_response endpoint.
    source_inquiry_uuid = Column(String(128), index=True)
    legacy_response_posted_at = Column(DateTime(timezone=True))
    legacy_attachment_url_count = Column(Integer, nullable=False, default=0)

    # When the InpharmD inquiry was a Medication-Use Evaluation with an Excel
    # attachment, we store the doc URL + the row this inquiry's manufacturer
    # lives in, so the email-reply hook can update the "Manufacturer Response"
    # column in that row and re-upload an updated copy.
    source_excel_url = Column(Text)
    source_excel_sheet = Column(String(255))
    source_excel_row = Column(Integer)
    # Per-row product details extracted from the MUE Excel alongside the manufacturer name.
    medication_name = Column(Text)
    pi_storage_data = Column(Text)
    # DailyMed-enriched fields: canonical PI URL and storage/handling text.
    pi_link = Column(Text)
    # Where the updated Excel landed (S3) after we filled in the response.
    excel_response_url = Column(Text)
    excel_response_posted_at = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    inbound_attachments = relationship(
        "InquiryAttachment",
        back_populates="inquiry",
        cascade="all, delete-orphan",
        order_by="InquiryAttachment.display_order",
    )
    email_replies = relationship(
        "EmailReply",
        back_populates="inquiry",
        cascade="all, delete-orphan",
        order_by="EmailReply.sent_at",
    )

    manufacturer = relationship("ManufacturerContact", back_populates="inquiries")


class EmailReply(Base):
    __tablename__ = "email_replies"

    id = Column(Integer, primary_key=True, index=True)
    inquiry_id = Column(
        Integer,
        ForeignKey("inquiries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction = Column(String(10), nullable=False)  # "inbound" | "outbound"
    sender_email = Column(String(255))
    body = Column(Text)
    sent_at = Column(DateTime(timezone=True), nullable=False)
    graph_message_id = Column(String(512))  # dedup key; unique per inquiry
    smtp_message_id = Column(String(512))   # RFC 2822 Message-ID; cross-path dedup key

    inquiry = relationship("Inquiry", back_populates="email_replies")
    attachments = relationship(
        "InquiryAttachment",
        back_populates="reply",
        order_by="InquiryAttachment.display_order",
    )


class InquiryAttachment(Base):
    __tablename__ = "inquiry_attachments"

    id = Column(Integer, primary_key=True, index=True)
    inquiry_id = Column(
        Integer,
        ForeignKey("inquiries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reply_id = Column(
        Integer,
        ForeignKey("email_replies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    url = Column(Text, nullable=False)
    filename = Column(String(512))
    content_type = Column(String(128))
    summary = Column(Text)
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    inquiry = relationship("Inquiry", back_populates="inbound_attachments")
    reply = relationship("EmailReply", back_populates="attachments")


class User(Base):
    """Local mirror of an authenticated InpharmD user.

    We never expose the upstream `access_token` to the browser. Instead we
    mint our own `session_token`, store both, and proxy staging API calls
    server-side keyed by the session token."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)

    # Random opaque token sent to the browser as X-Session-Token.
    session_token = Column(String(64), nullable=False, unique=True, index=True)

    # The InpharmD platform access_token returned by /v2/login. Never
    # leaves the server. Re-fetched on every login.
    staging_token = Column(Text, nullable=False)

    # Whatever the staging /v2/login response carries about the user. We
    # store the id + a JSON blob for display so we don't refetch on every
    # request.
    staging_user_id = Column(String(64))
    display_name = Column(String(255))
    profile_json = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), server_default=func.now())


class DailymedCache(Base):
    """Persistent cache for DailyMed NDC lookups (30-day TTL).

    Keyed by normalized NDC string.  fetched_at is used by the service to
    determine whether the entry is still within the TTL window.
    """
    __tablename__ = "dailymed_cache"

    ndc = Column(String(50), primary_key=True)
    setid = Column(String(128))          # DailyMed SPL set-id (UUID)
    pi_link = Column(Text)               # https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=<setid>
    pi_storage = Column(Text)            # Full text of section 34069-5
    fetched_at = Column(DateTime(timezone=True), nullable=False)
