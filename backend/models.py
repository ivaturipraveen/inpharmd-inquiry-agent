from sqlalchemy import (
    Boolean,
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
    fallback_call_enabled = Column(Boolean, nullable=False, default=False)
    notes = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    inquiries = relationship("Inquiry", back_populates="manufacturer", cascade="all, delete-orphan")


# Inquiry lifecycle: draft -> email_sent -> email_responded -> call_pending ->
# call_completed -> closed (or needs_attention/failed).
INQUIRY_STATUSES = (
    "draft",
    "email_pending",   # scheduled; scheduler will send at email_scheduled_for
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
    # Owner — the Outreach tab filters by this so each user sees only their own.
    # Nullable for legacy rows; the API treats unowned rows as invisible.
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    manufacturer_id = Column(
        Integer,
        ForeignKey("manufacturer_contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    subject = Column(String(1000), nullable=False)
    question = Column(Text, nullable=False)
    requester_name = Column(String(255))
    requester_email = Column(String(255))
    # Requesting pharmacist's team/org (e.g. "MedStar Health") — from InpharmD's
    # inquiry_submitter_details.team_name, or typed in manually.
    team_name = Column(Text)

    # Wait this many hours after the email is sent before falling back to a call.
    fallback_after_hours = Column(Integer, nullable=False, default=24)

    status = Column(String(32), nullable=False, default="draft", index=True)

    email_scheduled_for = Column(DateTime(timezone=True))
    email_sent_at = Column(DateTime(timezone=True))
    email_message_id = Column(String(255))
    email_response_at = Column(DateTime(timezone=True))
    email_response = Column(Text)

    # Groups inquiries from one bulk_create_inquiries call (email only) for a
    # single batch Slack notification — distinct from source_inquiry_uuid.
    bulk_batch_id = Column(String(64), index=True, nullable=True)

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

    # Set when an outbound-call request timed out with no response — while
    # non-null, unconditionally excludes this inquiry from auto retry/fallback.
    call_outcome_unknown_until = Column(DateTime(timezone=True), nullable=True)

    # Write-once: set at first real contact (email send or initial call) — starts
    # the 48h no-response window; never overwritten by later retries/fallback.
    first_contacted_at = Column(DateTime(timezone=True), nullable=True)
    # Set only after a confirmed "no response after 48h" Slack post (send-first-
    # then-mark) so the notification fires exactly once.
    no_response_notified_at = Column(DateTime(timezone=True), nullable=True)
    # Write-once, set only by close_inquiry — never touched by a later follow-up,
    # so the Timeline can place "Closed" correctly. Backfilled once in main.py.
    closed_at = Column(DateTime(timezone=True), nullable=True)

    # Reconciliation backoff tracking (scheduler._reconcile_stuck_calls), persisted
    # so it survives restarts; reset to 0/NULL once the call resolves.
    call_reconcile_failure_count = Column(Integer, nullable=False, default=0, server_default="0")
    call_reconcile_next_attempt_at = Column(DateTime(timezone=True), nullable=True)

    # True for inquiries created by the Test Call flow. These must never enter
    # the production manufacturer workflow: no retries, no Slack, no legacy POST.
    is_test_call = Column(Boolean, nullable=False, default=False, server_default="false")
    # The phone number that was dialed for a test call (always set when is_test_call=True).
    test_call_phone = Column(String(32))

    final_answer = Column(Text)

    # PDF attachment that came back with an email reply.
    # url = presigned/public link to the file in object storage (S3, R2, etc.)
    pdf_url = Column(Text)
    pdf_filename = Column(String(512))
    pdf_summary = Column(Text)

    # Set when forwarded from the InpharmD platform — stores the staging-side
    # UUID so we can POST the manufacturer's response back to the legacy endpoint.
    source_inquiry_uuid = Column(String(128), index=True)
    legacy_response_posted_at = Column(DateTime(timezone=True))
    legacy_attachment_url_count = Column(Integer, nullable=False, default=0, server_default="0")
    # Event key of the most recent successful legacy POST (e.g. "call:<id>" or
    # "email:<EmailReply.id>") — lets maybe_post_for_inquiry dedup exact repeats.
    legacy_last_event_key = Column(String(255), nullable=True)

    # For MUE inquiries with an Excel attachment — doc URL + row, so the email-
    # reply hook can update "Manufacturer Response" and re-upload.
    source_excel_url = Column(Text)
    source_excel_sheet = Column(String(255))
    source_excel_row = Column(Integer)
    # Per-row product details extracted from the MUE Excel alongside the manufacturer name.
    medication_name = Column(Text)
    pi_storage_data = Column(Text)
    # Raw "Temperature Excursion Request" text (API field mue_details), distinct
    # from question — batch-level, same for every target from one source inquiry.
    mue_details = Column(Text)
    # DailyMed-enriched fields: canonical PI URL and storage/handling text.
    pi_link = Column(Text)
    # Where the updated Excel landed (S3) after we filled in the response.
    excel_response_url = Column(Text)
    excel_response_posted_at = Column(DateTime(timezone=True))

    # All original attachments from the source InpharmD inquiry (JSON list of
    # {file_name, doc_url}) — independent of source_excel_url (the MUE workbook).
    source_attachments_json = Column(Text, nullable=True)
    # Cached structured-field extraction (JSON) from source_attachments_json —
    # written only on success (incl. "nothing found"), never on transient failure.
    attachment_extraction_cache = Column(Text, nullable=True)

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
    call_logs = relationship(
        "CallLog",
        back_populates="inquiry",
        cascade="all, delete-orphan",
        order_by="CallLog.started_at",
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


class CallLog(Base):
    """One row per physical call placed for an inquiry — the initial call,
    every auto-retry, every fallback call, and every follow-up call each get
    their own row, so none of them overwrite each other's transcript/summary
    the way the single-row Inquiry.call_* columns do. Those Inquiry.call_*
    columns are kept unchanged (still "the most recent call") for backward
    compatibility with existing code that reads them; CallLog is a purely
    additive, append-only ledger alongside them.

    completed_at is set by EITHER submit_answer (the agent's live, possibly
    partial mid-call result) or the post-call webhook/reconciliation/manual
    entry. resolved_at is set ONLY by the latter group — the "no further
    update expected for this call" marker — specifically so a post-call
    webhook carrying the full transcript is never mistaken for a duplicate
    delivery just because submit_answer already ran first (see
    call_log_service.find_call_log_for_completion and routers.webhooks).
    """

    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, index=True)
    inquiry_id = Column(
        Integer,
        ForeignKey("inquiries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id = Column(String(128), index=True, nullable=True)
    is_test_call = Column(Boolean, nullable=False, default=False, server_default="false")
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    provider_status = Column(String(64), nullable=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    inquiry = relationship("Inquiry", back_populates="call_logs")


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

    # Whatever the staging /v2/login response carries about the user — stored
    # so we don't refetch on every request.
    staging_user_id = Column(String(64))
    display_name = Column(String(255))
    profile_json = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), server_default=func.now())


class BulkEmailBatch(Base):
    """One row per bulk_create_inquiries email dispatch. Exists solely to
    guarantee the batch-completion Slack notification fires exactly once —
    completed_notified_at is only ever set immediately after a confirmed
    successful Slack post, never before."""
    __tablename__ = "bulk_email_batches"

    batch_id = Column(String(64), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_notified_at = Column(DateTime(timezone=True), nullable=True)


class UnmatchedCallWebhook(Base):
    """A post-call webhook from ElevenLabs that could not be matched to any
    inquiry — neither by call_conversation_id nor by the inquiry_id dynamic
    variable in the payload. Persisted so a valid answer is never silently
    discarded; requires manual investigation to reconcile."""
    __tablename__ = "unmatched_call_webhooks"

    id = Column(Integer, primary_key=True, index=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    conversation_id = Column(String(128), index=True)
    raw_payload = Column(Text, nullable=False)
    reason = Column(String(64), nullable=False)


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
