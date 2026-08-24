"""Tests for GET /api/inquiries `include_replies` param.

Covers:
  - Default (no param, or include_replies=true) still eager-loads and
    returns inbound_attachments / email_replies (+ reply attachments) —
    unchanged from before this param existed. The Emails tab depends on
    this: it reads reply content straight off the list response.
  - include_replies=false omits both fields from the response entirely,
    for every other field/value being otherwise identical.
  - The single-inquiry detail endpoint (GET /api/inquiries/{id}) is
    untouched and always returns full reply/attachment data regardless
    of what the list endpoint was asked for.

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_inquiry_list_include_replies.py -v
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db  # noqa: E402

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=engine)

from models import EmailReply, Inquiry, InquiryAttachment, ManufacturerContact, User  # noqa: E402
from main import app  # noqa: E402
from routers.auth import get_current_user  # noqa: E402

Base.metadata.create_all(bind=engine)


def _make_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestSession()


def _make_user(db) -> User:
    user = User(email="tester@example.com", session_token="tok", staging_token="stok")
    db.add(user)
    db.flush()
    return user


def _make_inquiry_with_reply_and_attachment(db, user) -> Inquiry:
    mfr = ManufacturerContact(manufacturer="Amgen", official_mi_email="amgen@example.com")
    db.add(mfr)
    db.flush()

    inq = Inquiry(
        user_id=user.id,
        manufacturer_id=mfr.id,
        subject="s", question="q",
        status="email_responded",
    )
    db.add(inq)
    db.flush()

    reply = EmailReply(
        inquiry_id=inq.id,
        direction="inbound",
        sender_email="amgen@example.com",
        body="Here is our answer.",
        sent_at=datetime.now(timezone.utc),
    )
    db.add(reply)
    db.flush()

    att = InquiryAttachment(
        inquiry_id=inq.id,
        reply_id=reply.id,
        url="https://example.com/file.pdf",
        filename="file.pdf",
        content_type="application/pdf",
        display_order=0,
    )
    db.add(att)
    db.commit()
    return inq


def _get_test_client(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _clear_overrides():
    app.dependency_overrides.clear()


class TestIncludeRepliesDefault:
    def test_default_includes_replies_and_attachments(self):
        db = _make_db()
        user = _make_user(db)
        inq = _make_inquiry_with_reply_and_attachment(db, user)

        client = _get_test_client(db, user)
        try:
            resp = client.get("/api/inquiries")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body) == 1
            item = body[0]
            assert item["id"] == inq.id
            assert "email_replies" in item
            assert len(item["email_replies"]) == 1
            assert item["email_replies"][0]["body"] == "Here is our answer."
            assert "inbound_attachments" in item
            assert len(item["inbound_attachments"]) == 1
            assert item["inbound_attachments"][0]["filename"] == "file.pdf"
            # Reply-scoped attachments are also present under the reply itself.
            assert len(item["email_replies"][0]["attachments"]) == 1
        finally:
            _clear_overrides()
        db.close()

    def test_explicit_include_replies_true_matches_default(self):
        db = _make_db()
        user = _make_user(db)
        _make_inquiry_with_reply_and_attachment(db, user)

        client = _get_test_client(db, user)
        try:
            resp = client.get("/api/inquiries?include_replies=true")
            assert resp.status_code == 200, resp.text
            item = resp.json()[0]
            assert len(item["email_replies"]) == 1
            assert len(item["inbound_attachments"]) == 1
        finally:
            _clear_overrides()
        db.close()


class TestIncludeRepliesFalse:
    def test_omits_replies_and_attachments(self):
        db = _make_db()
        user = _make_user(db)
        inq = _make_inquiry_with_reply_and_attachment(db, user)

        client = _get_test_client(db, user)
        try:
            resp = client.get("/api/inquiries?include_replies=false")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body) == 1
            item = body[0]
            assert item["id"] == inq.id
            assert "email_replies" not in item
            assert "inbound_attachments" not in item
        finally:
            _clear_overrides()
        db.close()

    def test_other_fields_unchanged_when_replies_omitted(self):
        """Every other field must be identical to the include_replies=true
        response — only the two relationship fields are dropped."""
        db = _make_db()
        user = _make_user(db)
        _make_inquiry_with_reply_and_attachment(db, user)

        client = _get_test_client(db, user)
        try:
            full = client.get("/api/inquiries?include_replies=true").json()[0]
            light = client.get("/api/inquiries?include_replies=false").json()[0]
            full_minus_replies = {
                k: v for k, v in full.items()
                if k not in ("email_replies", "inbound_attachments")
            }
            assert light == full_minus_replies
        finally:
            _clear_overrides()
        db.close()

    def test_works_with_all_users(self):
        """all_users=true builds InquiryOut/InquiryListOut manually (for the
        created_by annotation) — include_replies must still be honored on
        that code path, not just the simpler single-owner path."""
        db = _make_db()
        user = _make_user(db)
        _make_inquiry_with_reply_and_attachment(db, user)

        client = _get_test_client(db, user)
        try:
            resp = client.get("/api/inquiries?all_users=true&include_replies=false")
            assert resp.status_code == 200, resp.text
            item = resp.json()[0]
            assert "email_replies" not in item
            assert "inbound_attachments" not in item
            assert item.get("created_by")  # all_users path still annotates creator
        finally:
            _clear_overrides()
        db.close()


class TestDetailEndpointUnaffected:
    def test_detail_endpoint_always_returns_full_replies_and_attachments(self):
        """GET /api/inquiries/{id} must be untouched by the list-side change —
        this is what the Outreach page's row-click handler relies on for the
        full detail modal, regardless of how the list was fetched."""
        db = _make_db()
        user = _make_user(db)
        inq = _make_inquiry_with_reply_and_attachment(db, user)

        client = _get_test_client(db, user)
        try:
            # Fetch the list with replies omitted first, to prove the detail
            # endpoint doesn't inherit that state from anywhere shared.
            client.get("/api/inquiries?include_replies=false")

            resp = client.get(f"/api/inquiries/{inq.id}")
            assert resp.status_code == 200, resp.text
            item = resp.json()
            assert len(item["email_replies"]) == 1
            assert item["email_replies"][0]["body"] == "Here is our answer."
            assert len(item["inbound_attachments"]) == 1
            assert item["inbound_attachments"][0]["filename"] == "file.pdf"
        finally:
            _clear_overrides()
        db.close()
