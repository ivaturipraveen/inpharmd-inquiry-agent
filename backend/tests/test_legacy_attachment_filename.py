"""Regression tests for the attachment-filename bug: legacy_response_service
must send InpharmD the manufacturer's original attachment filename
(InquiryAttachment.filename), not the mangled S3 key derived from the URL
(which carries our own uuid4().hex[:10] uniqueness prefix — see
s3_service.upload_bytes).

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_legacy_attachment_filename.py -v
"""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("LEGACY_RESPONSE_API_KEY", "test-key")
os.environ.setdefault("INPHARMD_API_BASE_URL", "http://fake-staging")

from database import Base, get_db  # noqa: E402
from models import EmailReply, Inquiry, InquiryAttachment, ManufacturerContact  # noqa: E402
import legacy_response_service  # noqa: E402

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=engine)
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _make_db():
    return TestSession()


# ---------------------------------------------------------------------------
# Unit tests: _download_attachment / post_response filename resolution
# ---------------------------------------------------------------------------

class _CapturingTransport(httpx.BaseTransport):
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)


def _inject_transport(transport: _CapturingTransport):
    original = httpx.Client

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return original(transport=transport, **kwargs)

    return factory


def _outbound_filename(request: httpx.Request) -> str:
    """Pull the filename= param off the (only) mfr_attachment[] part."""
    ct = request.headers.get("content-type", "")
    boundary = next(
        s[len("boundary="):].strip('"')
        for s in (p.strip() for p in ct.split(";"))
        if s.startswith("boundary=")
    )
    body = bytes(request.content)
    for raw in body.split(f"--{boundary}".encode()):
        raw = raw.lstrip(b"\r\n")
        if b"mfr_attachment[]" not in raw or b"filename=" not in raw:
            continue
        header_block = raw.split(b"\r\n\r\n", 1)[0]
        for line in header_block.split(b"\r\n"):
            if b"filename=" in line:
                return line.decode().split("filename=", 1)[1].strip('"')
    raise AssertionError("no mfr_attachment[] part with a filename found")


class TestDownloadAttachmentFilenameResolution:
    def test_uses_given_filename_over_url_derived_one(self):
        """The core bug: a mangled S3 URL must not leak into the outbound name
        when the original filename is known."""
        with patch("legacy_response_service.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = httpx.Response(
                200, content=b"data", headers={"content-type": "application/octet-stream"},
            )
            result = legacy_response_service._download_attachment(
                "https://s3.example.com/inquiry-pdfs/inquiry-42/a9143b26f2-143sample_users.xlsx",
                filename="143sample_users.xlsx",
            )
        assert result is not None
        filename, _content, _content_type = result
        assert filename == "143sample_users.xlsx"

    def test_falls_back_to_url_derived_name_when_no_filename_given(self):
        with patch("legacy_response_service.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value.__enter__.return_value
            mock_client.get.return_value = httpx.Response(
                200, content=b"data", headers={"content-type": "application/pdf"},
            )
            result = legacy_response_service._download_attachment(
                "https://s3.example.com/inquiry-pdfs/inquiry-42/a9143b26f2-report.pdf",
            )
        assert result is not None
        filename, _content, _content_type = result
        assert filename == "a9143b26f2-report.pdf"


class TestPostResponseAttachmentSpecs:
    def test_url_filename_pair_sends_original_filename(self):
        transport = _CapturingTransport()
        with patch(
            "legacy_response_service._download_attachment",
            return_value=("143sample_users.xlsx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ) as mock_download:
            with patch("legacy_response_service.httpx.Client", _inject_transport(transport)):
                ok = legacy_response_service.post_response(
                    inquiry_uuid="uuid-1",
                    mfr_email_response="See attached.",
                    mfr_attachment=[
                        ("https://s3.example.com/inquiry-pdfs/inquiry-42/a9143b26f2-143sample_users.xlsx", "143sample_users.xlsx"),
                    ],
                )
        assert ok is True
        mock_download.assert_called_once_with(
            "https://s3.example.com/inquiry-pdfs/inquiry-42/a9143b26f2-143sample_users.xlsx",
            "143sample_users.xlsx",
        )
        assert _outbound_filename(transport.requests[0]) == "143sample_users.xlsx"

    def test_plain_url_string_still_works_and_falls_back(self):
        """Backward compatibility: a caller that only has a bare URL (no
        stored filename) still works exactly as before."""
        transport = _CapturingTransport()
        with patch(
            "legacy_response_service._download_attachment",
            return_value=("report.pdf", b"%PDF", "application/pdf"),
        ) as mock_download:
            with patch("legacy_response_service.httpx.Client", _inject_transport(transport)):
                ok = legacy_response_service.post_response(
                    inquiry_uuid="uuid-2",
                    mfr_email_response="See attached.",
                    mfr_attachment=["https://s3.example.com/report.pdf"],
                )
        assert ok is True
        mock_download.assert_called_once_with("https://s3.example.com/report.pdf", None)
        assert _outbound_filename(transport.requests[0]) == "report.pdf"

    def test_mixed_list_of_plain_urls_and_pairs(self):
        transport = _CapturingTransport()
        with patch(
            "legacy_response_service._download_attachment",
            side_effect=[
                ("143sample_users.xlsx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                ("a9143b26f2-report.pdf", b"%PDF", "application/pdf"),
            ],
        ) as mock_download:
            with patch("legacy_response_service.httpx.Client", _inject_transport(transport)):
                ok = legacy_response_service.post_response(
                    inquiry_uuid="uuid-3",
                    mfr_email_response="See attached.",
                    mfr_attachment=[
                        ("https://s3.example.com/a9143b26f2-143sample_users.xlsx", "143sample_users.xlsx"),
                        "https://s3.example.com/a9143b26f2-report.pdf",
                    ],
                )
        assert ok is True
        assert mock_download.call_count == 2
        mock_download.assert_any_call(
            "https://s3.example.com/a9143b26f2-143sample_users.xlsx", "143sample_users.xlsx",
        )
        mock_download.assert_any_call("https://s3.example.com/a9143b26f2-report.pdf", None)


# ---------------------------------------------------------------------------
# Integration test: maybe_post_for_inquiry threads InquiryAttachment.filename
# all the way through to the outbound multipart request.
# ---------------------------------------------------------------------------

class TestMaybePostForInquiryAttachmentFilenames:
    def test_sends_original_filename_not_mangled_s3_key(self):
        db = _make_db()
        mfr = ManufacturerContact(manufacturer="Accord Healthcare", official_mi_email="mi@accord.com")
        db.add(mfr); db.flush()
        inq = Inquiry(
            manufacturer_id=mfr.id,
            subject="Drug stability question [InpharmD #1]",
            question="Is it stable at 25C?",
            source_inquiry_uuid="uuid-real-inquiry",
            status="email_responded",
        )
        db.add(inq); db.flush()
        reply = EmailReply(
            inquiry_id=inq.id, direction="inbound", sender_email="mfr@accord.com",
            body="Yes, stable per our data.",
            sent_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(reply); db.flush()
        db.add(InquiryAttachment(
            inquiry_id=inq.id,
            reply_id=reply.id,
            url="https://s3.example.com/inquiry-pdfs/inquiry-%d/a9143b26f2-143sample_users.xlsx" % inq.id,
            filename="143sample_users.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            display_order=0,
        ))
        db.commit()

        transport = _CapturingTransport()
        with patch(
            "legacy_response_service._download_attachment",
            return_value=("143sample_users.xlsx", b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ) as mock_download:
            with patch("legacy_response_service.httpx.Client", _inject_transport(transport)):
                ok = legacy_response_service.maybe_post_for_inquiry(
                    db, inq, f"email:{reply.id}", email_reply_id=reply.id,
                )

        assert ok is True
        called_url, called_filename = mock_download.call_args[0]
        assert called_filename == "143sample_users.xlsx"
        assert "a9143b26f2" in called_url  # the mangled key is fine to keep — it's just the URL
        assert _outbound_filename(transport.requests[0]) == "143sample_users.xlsx"

    def test_attachment_with_no_stored_filename_falls_back_to_url(self):
        """Defensive fallback for any pre-existing row that (for whatever
        reason) never had a filename recorded."""
        db = _make_db()
        mfr = ManufacturerContact(manufacturer="Accord Healthcare", official_mi_email="mi@accord.com")
        db.add(mfr); db.flush()
        inq = Inquiry(
            manufacturer_id=mfr.id,
            subject="Drug stability question [InpharmD #2]",
            question="Is it stable at 25C?",
            source_inquiry_uuid="uuid-real-inquiry-2",
            status="email_responded",
        )
        db.add(inq); db.flush()
        reply = EmailReply(
            inquiry_id=inq.id, direction="inbound", sender_email="mfr@accord.com",
            body="Yes, stable per our data.",
            sent_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        db.add(reply); db.flush()
        db.add(InquiryAttachment(
            inquiry_id=inq.id,
            reply_id=reply.id,
            url="https://s3.example.com/inquiry-pdfs/inquiry-%d/a9143b26f2-report.pdf" % inq.id,
            filename=None,
            content_type="application/pdf",
            display_order=0,
        ))
        db.commit()

        transport = _CapturingTransport()
        with patch(
            "legacy_response_service._download_attachment",
            return_value=("a9143b26f2-report.pdf", b"%PDF", "application/pdf"),
        ) as mock_download:
            with patch("legacy_response_service.httpx.Client", _inject_transport(transport)):
                ok = legacy_response_service.maybe_post_for_inquiry(
                    db, inq, f"email:{reply.id}", email_reply_id=reply.id,
                )

        assert ok is True
        called_url, called_filename = mock_download.call_args[0]
        assert called_filename is None
        assert _outbound_filename(transport.requests[0]) == "a9143b26f2-report.pdf"
