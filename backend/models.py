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
    manufacturer_id = Column(
        Integer,
        ForeignKey("manufacturer_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subject = Column(String(255), nullable=False)
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

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    manufacturer = relationship("ManufacturerContact", back_populates="inquiries")
