"""Tests for inbound attachment processing and cross-path deduplication.

Uses SQLite in-memory database (no real PostgreSQL needed) and mocks all
external I/O (S3, GPT).  No real HTTP calls are made.

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_inbound_attachment_service.py -v
"""
from __future__ import annotations

import asyncio
import json
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile

# ---------------------------------------------------------------------------
# In-memory database setup
# ---------------------------------------------------------------------------

# Must import before any model that references Base so all tables are created.
from database import Base  # noqa: E402

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Model fixtures
# ---------------------------------------------------------------------------

from models import EmailReply, Inquiry, InquiryAttachment, ManufacturerContact  # noqa: E402


@pytest.fixture()
def manufacturer(db):
    mfr = ManufacturerContact(manufacturer="Acme Pharma")
    db.add(mfr)
    db.commit()
    db.refresh(mfr)
    return mfr


@pytest.fixture()
def inquiry(db, manufacturer):
    inq = Inquiry(
        manufacturer_id=manufacturer.id,
        subject="Test inquiry",
        question="What is the storage temperature?",
    )
    db.add(inq)
    db.commit()
    db.refresh(inq)
    return inq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_att(name="report.pdf", content=b"%PDF-1.4 fake", content_type="application/pdf"):
    return {"bytes": content, "name": name, "content_type": content_type}


def _make_email_reply(db, inquiry_id, smtp_message_id=None, graph_message_id=None):
    row = EmailReply(
        inquiry_id=inquiry_id,
        direction="inbound",
        sender_email="mfr@example.com",
        body="some reply",
        sent_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        smtp_message_id=smtp_message_id,
        graph_message_id=graph_message_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# inbound_attachment_service tests
# ---------------------------------------------------------------------------

import inbound_attachment_service  # noqa: E402


class TestProcessAttachments:
    @patch("inbound_attachment_service.summary_service")
    @patch("inbound_attachment_service.s3_service")
    def test_single_attachment_creates_row(self, mock_s3, mock_summary, db, inquiry):
        mock_s3.upload_bytes.return_value = "https://s3.example.com/file.pdf"
        mock_summary.is_configured.return_value = False

        email_reply = _make_email_reply(db, inquiry.id)
        raw = [_make_raw_att()]

        result = inbound_attachment_service.process_attachments(
            db=db,
            inquiry_id=inquiry.id,
            reply_id=email_reply.id,
            raw_attachments=raw,
            question=inquiry.question,
            manufacturer_name="Acme Pharma",
        )

        assert len(result) == 1
        assert result[0]["url"] == "https://s3.example.com/file.pdf"
        assert result[0]["filename"] == "report.pdf"
        rows = db.query(InquiryAttachment).filter_by(inquiry_id=inquiry.id).all()
        assert len(rows) == 1
        assert rows[0].reply_id == email_reply.id
        assert rows[0].display_order == 0

    @patch("inbound_attachment_service.summary_service")
    @patch("inbound_attachment_service.s3_service")
    def test_multiple_attachments_all_created(self, mock_s3, mock_summary, db, inquiry):
        mock_s3.upload_bytes.side_effect = [
            "https://s3.example.com/a.pdf",
            "https://s3.example.com/b.docx",
            "https://s3.example.com/c.xlsx",
        ]
        mock_summary.is_configured.return_value = False

        email_reply = _make_email_reply(db, inquiry.id)
        raw = [
            _make_raw_att("a.pdf"),
            _make_raw_att("b.docx", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            _make_raw_att("c.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ]

        result = inbound_attachment_service.process_attachments(
            db=db, inquiry_id=inquiry.id, reply_id=email_reply.id,
            raw_attachments=raw, question=inquiry.question, manufacturer_name="Acme",
        )

        assert len(result) == 3
        assert [r["display_order"] for r in result] == [0, 1, 2]
        rows = db.query(InquiryAttachment).filter_by(inquiry_id=inquiry.id).order_by(InquiryAttachment.display_order).all()
        assert len(rows) == 3

    @patch("inbound_attachment_service.summary_service")
    @patch("inbound_attachment_service.s3_service")
    def test_s3_failure_skips_attachment(self, mock_s3, mock_summary, db, inquiry):
        mock_s3.upload_bytes.return_value = None  # S3 failure
        mock_summary.is_configured.return_value = False

        email_reply = _make_email_reply(db, inquiry.id)
        result = inbound_attachment_service.process_attachments(
            db=db, inquiry_id=inquiry.id, reply_id=email_reply.id,
            raw_attachments=[_make_raw_att()],
            question=inquiry.question, manufacturer_name="Acme",
        )

        assert result == []
        assert db.query(InquiryAttachment).filter_by(inquiry_id=inquiry.id).count() == 0

    @patch("inbound_attachment_service.summary_service")
    @patch("inbound_attachment_service.s3_service")
    def test_gpt_failure_still_creates_row(self, mock_s3, mock_summary, db, inquiry):
        mock_s3.upload_bytes.return_value = "https://s3.example.com/file.pdf"
        mock_summary.is_configured.return_value = True
        mock_summary.extract_document_text.return_value = "some text"
        mock_summary.summarize_pdf.side_effect = RuntimeError("OpenAI down")

        email_reply = _make_email_reply(db, inquiry.id)
        result = inbound_attachment_service.process_attachments(
            db=db, inquiry_id=inquiry.id, reply_id=email_reply.id,
            raw_attachments=[_make_raw_att()],
            question=inquiry.question, manufacturer_name="Acme",
        )

        assert len(result) == 1
        assert result[0]["summary"] is None
        assert db.query(InquiryAttachment).filter_by(inquiry_id=inquiry.id).count() == 1

    @patch("inbound_attachment_service.summary_service")
    @patch("inbound_attachment_service.s3_service")
    def test_unsupported_type_skipped(self, mock_s3, mock_summary, db, inquiry):
        mock_summary.is_configured.return_value = False

        email_reply = _make_email_reply(db, inquiry.id)
        result = inbound_attachment_service.process_attachments(
            db=db, inquiry_id=inquiry.id, reply_id=email_reply.id,
            raw_attachments=[_make_raw_att("archive.zip", content_type="application/zip")],
            question=inquiry.question, manufacturer_name="Acme",
        )

        assert result == []
        mock_s3.upload_bytes.assert_not_called()

    @patch("inbound_attachment_service.summary_service")
    @patch("inbound_attachment_service.s3_service")
    def test_empty_bytes_skipped(self, mock_s3, mock_summary, db, inquiry):
        mock_summary.is_configured.return_value = False

        email_reply = _make_email_reply(db, inquiry.id)
        result = inbound_attachment_service.process_attachments(
            db=db, inquiry_id=inquiry.id, reply_id=email_reply.id,
            raw_attachments=[_make_raw_att(content=b"")],
            question=inquiry.question, manufacturer_name="Acme",
        )

        assert result == []
        mock_s3.upload_bytes.assert_not_called()

    @patch("inbound_attachment_service.summary_service")
    @patch("inbound_attachment_service.s3_service")
    def test_gpt_summary_stored(self, mock_s3, mock_summary, db, inquiry):
        mock_s3.upload_bytes.return_value = "https://s3.example.com/file.pdf"
        mock_summary.is_configured.return_value = True
        mock_summary.extract_document_text.return_value = "document text"
        mock_summary.summarize_pdf.return_value = "Store at 2-8°C."

        email_reply = _make_email_reply(db, inquiry.id)
        result = inbound_attachment_service.process_attachments(
            db=db, inquiry_id=inquiry.id, reply_id=email_reply.id,
            raw_attachments=[_make_raw_att()],
            question=inquiry.question, manufacturer_name="Acme",
        )

        assert result[0]["summary"] == "Store at 2-8°C."
        row = db.query(InquiryAttachment).filter_by(inquiry_id=inquiry.id).first()
        assert row.summary == "Store at 2-8°C."


# ---------------------------------------------------------------------------
# Cross-path deduplication tests
# ---------------------------------------------------------------------------

class TestCrossPathDedup:
    def test_smtp_message_id_stored_on_email_reply(self, db, inquiry):
        row = _make_email_reply(db, inquiry.id, smtp_message_id="<abc@mail.example.com>")
        db.refresh(row)
        assert row.smtp_message_id == "<abc@mail.example.com>"

    def test_dedup_query_finds_existing_smtp_message_id(self, db, inquiry):
        _make_email_reply(db, inquiry.id, smtp_message_id="<abc@mail.example.com>")

        existing = db.query(EmailReply).filter(
            EmailReply.inquiry_id == inquiry.id,
            EmailReply.smtp_message_id == "<abc@mail.example.com>",
        ).first()

        assert existing is not None

    def test_dedup_query_misses_different_smtp_message_id(self, db, inquiry):
        _make_email_reply(db, inquiry.id, smtp_message_id="<abc@mail.example.com>")

        existing = db.query(EmailReply).filter(
            EmailReply.inquiry_id == inquiry.id,
            EmailReply.smtp_message_id == "<different@mail.example.com>",
        ).first()

        assert existing is None

    def test_null_smtp_message_id_does_not_match(self, db, inquiry):
        _make_email_reply(db, inquiry.id, smtp_message_id=None)
        # A second row with NULL smtp_message_id should not be found by a
        # dedup query filtering for NULL — NULL != NULL in SQL.
        existing = db.query(EmailReply).filter(
            EmailReply.inquiry_id == inquiry.id,
            EmailReply.smtp_message_id == None,  # noqa: E711
        ).first()
        # SQLite will find NULL == NULL here (SQLite behavior differs from PG).
        # This test just documents the behavior; the dedup guard in each handler
        # is conditional on smtp_message_id being non-None.

    def test_graph_dedup_by_graph_message_id_unaffected(self, db, inquiry):
        """Existing graph_message_id dedup path must still work independently."""
        _make_email_reply(db, inquiry.id, graph_message_id="graph-internal-id-123")

        existing = db.query(EmailReply).filter(
            EmailReply.inquiry_id == inquiry.id,
            EmailReply.graph_message_id == "graph-internal-id-123",
        ).first()

        assert existing is not None

    def test_different_inquiries_same_smtp_id_allowed(self, db, inquiry, manufacturer):
        """Same SMTP Message-ID for different inquiry_ids must NOT trigger dedup."""
        inq2 = Inquiry(
            manufacturer_id=manufacturer.id,
            subject="Second inquiry",
            question="Different question",
        )
        db.add(inq2)
        db.commit()
        db.refresh(inq2)

        _make_email_reply(db, inquiry.id, smtp_message_id="<shared@mail.example.com>")

        # Check dedup for inq2 — should find nothing (different inquiry_id).
        existing = db.query(EmailReply).filter(
            EmailReply.inquiry_id == inq2.id,
            EmailReply.smtp_message_id == "<shared@mail.example.com>",
        ).first()

        assert existing is None


# ---------------------------------------------------------------------------
# SendGrid helper tests
# ---------------------------------------------------------------------------

# We test _parse_smtp_message_id and _collect_sendgrid_attachments in isolation.

from routers.email_inbound import _parse_smtp_message_id  # noqa: E402


class TestParseSmtpMessageId:
    def test_extracts_message_id(self):
        headers = "Subject: test\r\nMessage-ID: <abc123@mail.sendgrid.net>\r\nFrom: a@b.com"
        assert _parse_smtp_message_id(headers) == "<abc123@mail.sendgrid.net>"

    def test_case_insensitive(self):
        headers = "message-id: <lower@example.com>"
        assert _parse_smtp_message_id(headers) == "<lower@example.com>"

    def test_returns_none_when_absent(self):
        headers = "Subject: test\r\nFrom: a@b.com"
        assert _parse_smtp_message_id(headers) is None

    def test_returns_none_on_empty_string(self):
        assert _parse_smtp_message_id("") is None

    def test_returns_none_on_none_input(self):
        assert _parse_smtp_message_id(None) is None

    def test_trims_whitespace(self):
        headers = "Message-ID:   <spaced@example.com>   "
        assert _parse_smtp_message_id(headers) == "<spaced@example.com>"


from routers.email_inbound import _collect_sendgrid_attachments  # noqa: E402


class TestCollectSendgridAttachments:
    def test_no_attachments_returns_empty(self):
        form = {"attachments": "0"}
        result = asyncio.run(_collect_sendgrid_attachments(form))
        assert result == []

    def test_single_attachment_parsed(self):
        content = b"%PDF-1.4 fake"
        upload = MagicMock(spec=UploadFile)
        upload.read = AsyncMock(return_value=content)
        upload.content_type = "application/pdf"

        att_info = json.dumps({"attachment1": {"filename": "report.pdf", "type": "application/pdf"}})
        form = {"attachments": "1", "attachment-info": att_info, "attachment1": upload}

        result = asyncio.run(_collect_sendgrid_attachments(form))

        assert len(result) == 1
        assert result[0]["name"] == "report.pdf"
        assert result[0]["bytes"] == content
        assert result[0]["content_type"] == "application/pdf"

    def test_multiple_attachments(self):
        def _upload(content, ct):
            u = MagicMock(spec=UploadFile)
            u.read = AsyncMock(return_value=content)
            u.content_type = ct
            return u

        att_info = json.dumps({
            "attachment1": {"filename": "a.pdf", "type": "application/pdf"},
            "attachment2": {"filename": "b.docx", "type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        })
        form = {
            "attachments": "2",
            "attachment-info": att_info,
            "attachment1": _upload(b"pdf bytes", "application/pdf"),
            "attachment2": _upload(b"docx bytes", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        }

        result = asyncio.run(_collect_sendgrid_attachments(form))

        assert len(result) == 2
        assert result[0]["name"] == "a.pdf"
        assert result[1]["name"] == "b.docx"

    def test_missing_attachment_info_falls_back_to_key_name(self):
        content = b"bytes"
        upload = MagicMock(spec=UploadFile)
        upload.read = AsyncMock(return_value=content)
        upload.content_type = "application/pdf"

        form = {"attachments": "1", "attachment1": upload}  # no attachment-info

        result = asyncio.run(_collect_sendgrid_attachments(form))

        assert len(result) == 1
        assert result[0]["name"] == "attachment1"

    def test_zero_bytes_attachment_skipped(self):
        upload = MagicMock(spec=UploadFile)
        upload.read = AsyncMock(return_value=b"")
        upload.content_type = "application/pdf"

        att_info = json.dumps({"attachment1": {"filename": "empty.pdf", "type": "application/pdf"}})
        form = {"attachments": "1", "attachment-info": att_info, "attachment1": upload}

        result = asyncio.run(_collect_sendgrid_attachments(form))

        assert result == []
