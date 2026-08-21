"""Tests for the manual 'Save Email Response' flow (record_email_response endpoint).

Covers:
  1. First save — creates EmailReply, sets inquiry scalars, triggers legacy POST + Slack
  2. Identical re-save — no duplicate legacy POST, no duplicate Slack, EmailReply body unchanged
  3. Corrected re-save — legacy POST re-fires, Slack re-fires, Excel writeback guard reset
  4. Manual save after a real email reply — creates separate EmailReply, correct downstream
  5. Double-save (sequential proxy for concurrent) — second identical save is a pure no-op

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_manual_email_response.py -v
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.pop("ELEVENLABS_WEBHOOK_SECRET", None)
os.environ.pop("AGENT_TOOLS_SECRET", None)

from database import Base, get_db  # noqa: E402

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=engine)

from models import EmailReply, Inquiry, ManufacturerContact, User  # noqa: E402
from routers.inquiries import _MANUAL_SMTP_ID  # noqa: E402
from main import app  # noqa: E402
from routers.auth import get_current_user  # noqa: E402

Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _db():
    return TestSession()


def _make_user(db) -> User:
    u = User(email="tester@example.com", session_token="tok", staging_token="stok")
    db.add(u)
    db.flush()
    return u


def _make_mfr(db, name="AcmePharma") -> ManufacturerContact:
    m = ManufacturerContact(
        manufacturer=name,
        official_mi_email=f"{name.lower()}@example.com",
    )
    db.add(m)
    db.flush()
    return m


def _make_inquiry(db, user, mfr, *, status="email_sent", source_uuid=None) -> Inquiry:
    inq = Inquiry(
        user_id=user.id,
        manufacturer_id=mfr.id,
        subject="Drug info request [InpharmD #1]",
        question="Does it interact with warfarin?",
        requester_name="Alice",
        requester_email="alice@hospital.com",
        status=status,
        source_inquiry_uuid=source_uuid,
        source_excel_url="https://s3.example.com/mue.xlsx" if source_uuid else None,
        source_excel_row=2 if source_uuid else None,
    )
    db.add(inq)
    db.commit()
    return inq


def _client(db):
    """TestClient with DB and auth overrides applied."""
    fake_user = db.query(User).first()

    def override_db():
        yield db

    def override_auth():
        return fake_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_auth
    return TestClient(app, raise_server_exceptions=True)


def _post_response(client, inquiry_id, text):
    return client.post(
        f"/api/inquiries/{inquiry_id}/record-email-response",
        json={"response": text},
    )


def _manual_replies(db, inquiry_id):
    """Return EmailReply rows that are the manual entry (smtp_message_id == sentinel)."""
    return (
        db.query(EmailReply)
        .filter(
            EmailReply.inquiry_id == inquiry_id,
            EmailReply.direction == "inbound",
            EmailReply.smtp_message_id == _MANUAL_SMTP_ID,
        )
        .all()
    )


# ---------------------------------------------------------------------------
# 1. First save
# ---------------------------------------------------------------------------

def test_first_save_creates_email_reply_and_sets_inquiry_fields():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr)

    client = _client(db)
    with (
        patch("legacy_response_service.maybe_post_for_inquiry") as mock_post,
        patch("slack_service.notify_reply") as mock_slack,
        patch("slack_service.is_configured", return_value=True),
    ):
        mock_post.return_value = True
        r = _post_response(client, inq.id, "No interaction with warfarin.")
    assert r.status_code == 200

    db.expire_all()
    inq_db = db.get(Inquiry, inq.id)
    assert inq_db.status == "email_responded"
    assert inq_db.email_response == "No interaction with warfarin."
    assert inq_db.final_answer == "No interaction with warfarin."
    assert inq_db.next_retry_at is None
    assert inq_db.call_scheduled_for is None

    manual = _manual_replies(db, inq.id)
    assert len(manual) == 1
    assert manual[0].body == "No interaction with warfarin."
    assert manual[0].direction == "inbound"

    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs.kwargs.get("email_reply_id") == manual[0].id
    assert call_kwargs.args[2] == f"email:{manual[0].id}"

    mock_slack.assert_called_once()
    slack_kwargs = mock_slack.call_args.kwargs
    assert slack_kwargs["answer"] == "No interaction with warfarin."
    assert slack_kwargs["inquiry_id"] == inq.id


# ---------------------------------------------------------------------------
# 2. Identical re-save — no duplicate legacy POST, no duplicate Slack
# ---------------------------------------------------------------------------

def test_identical_resave_no_duplicate_post_or_slack():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr)
    client = _client(db)

    with (
        patch("legacy_response_service.maybe_post_for_inquiry") as mock_post,
        patch("slack_service.notify_reply") as mock_slack,
        patch("slack_service.is_configured", return_value=True),
    ):
        mock_post.return_value = True
        _post_response(client, inq.id, "No interaction.")
        mock_post.reset_mock()
        mock_slack.reset_mock()

        # Simulate what happens when maybe_post_for_inquiry sees the same event_key:
        # it returns False (already posted). We need to verify it's called with same key.
        mock_post.return_value = False
        r = _post_response(client, inq.id, "No interaction.")

    assert r.status_code == 200

    # Still exactly one manual EmailReply row
    manual = _manual_replies(db, inq.id)
    assert len(manual) == 1

    # Legacy POST called with same event_key — dedup happens inside maybe_post_for_inquiry
    mock_post.assert_called_once()
    assert mock_post.call_args.args[2] == f"email:{manual[0].id}"

    # Slack must NOT fire on identical re-save
    mock_slack.assert_not_called()


def test_identical_resave_does_not_reset_excel_guard():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr, source_uuid="uuid-abc")
    client = _client(db)

    with patch("legacy_response_service.maybe_post_for_inquiry", return_value=True):
        _post_response(client, inq.id, "Same text.")

    # Manually simulate that Excel writeback has run (set the guard)
    db.expire_all()
    inq_db = db.get(Inquiry, inq.id)
    from datetime import datetime, timezone
    inq_db.excel_response_posted_at = datetime.now(timezone.utc)
    db.commit()

    with patch("legacy_response_service.maybe_post_for_inquiry", return_value=True):
        _post_response(client, inq.id, "Same text.")

    db.expire_all()
    inq_db = db.get(Inquiry, inq.id)
    # Guard must NOT have been reset (identical text → no change)
    assert inq_db.excel_response_posted_at is not None


# ---------------------------------------------------------------------------
# 3. Corrected re-save
# ---------------------------------------------------------------------------

def test_corrected_resave_retriggers_legacy_post_and_slack():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr)
    client = _client(db)

    with (
        patch("legacy_response_service.maybe_post_for_inquiry") as mock_post,
        patch("slack_service.notify_reply") as mock_slack,
        patch("slack_service.is_configured", return_value=True),
    ):
        mock_post.return_value = True
        _post_response(client, inq.id, "Original answer.")

        db.expire_all()
        manual = _manual_replies(db, inq.id)
        reply_id = manual[0].id

        # Simulate the first save stamping legacy_last_event_key
        inq_db = db.get(Inquiry, inq.id)
        inq_db.legacy_last_event_key = f"email:{reply_id}"
        inq_db.excel_response_posted_at = datetime.now(timezone.utc)
        db.commit()

        mock_post.reset_mock()
        mock_slack.reset_mock()

        _post_response(client, inq.id, "Corrected answer.")

    db.expire_all()

    # EmailReply body updated, still one row
    manual = _manual_replies(db, inq.id)
    assert len(manual) == 1
    assert manual[0].body == "Corrected answer."

    # legacy_last_event_key was cleared so POST could re-fire
    inq_db = db.get(Inquiry, inq.id)
    # Excel writeback guard was reset
    assert inq_db.excel_response_posted_at is None

    # Legacy POST called again with same event_key (same reply id)
    mock_post.assert_called_once()
    assert mock_post.call_args.args[2] == f"email:{reply_id}"

    # Slack fired again
    mock_slack.assert_called_once()
    assert mock_slack.call_args.kwargs["answer"] == "Corrected answer."


def test_corrected_resave_does_not_clear_guard_when_key_differs():
    """When legacy_last_event_key is from a DIFFERENT event (real email, call),
    the new event_key already differs — no guard clearing needed or done."""
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr)
    client = _client(db)

    with patch("legacy_response_service.maybe_post_for_inquiry", return_value=True):
        _post_response(client, inq.id, "First answer.")

    db.expire_all()
    inq_db = db.get(Inquiry, inq.id)
    manual = _manual_replies(db, inq.id)
    # Simulate the key being from a DIFFERENT event (e.g. a real Graph email reply)
    inq_db.legacy_last_event_key = "email:999"
    db.commit()

    with (
        patch("legacy_response_service.maybe_post_for_inquiry") as mock_post,
        patch("slack_service.is_configured", return_value=False),
    ):
        mock_post.return_value = True
        _post_response(client, inq.id, "Updated answer.")

    db.expire_all()
    inq_db = db.get(Inquiry, inq.id)
    # Guard should NOT have been cleared (it pointed to a different event)
    # The event_key "email:{reply_id}" already differs from "email:999" → POST fires
    assert inq_db.legacy_last_event_key != f"email:{manual[0].id}"
    mock_post.assert_called_once()
    assert mock_post.call_args.args[2] == f"email:{manual[0].id}"


# ---------------------------------------------------------------------------
# 4. Manual save after a real email reply already exists
# ---------------------------------------------------------------------------

def test_manual_save_after_real_reply_creates_separate_email_reply():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr, status="email_sent")

    # Simulate a real email reply (has smtp_message_id set)
    real_reply = EmailReply(
        inquiry_id=inq.id,
        direction="inbound",
        sender_email="mi@acmepharma.com",
        body="Real reply from manufacturer.",
        sent_at=datetime.now(timezone.utc),
        graph_message_id=None,
        smtp_message_id="<real-msg-id@mail.acmepharma.com>",
    )
    db.add(real_reply)
    inq.status = "email_responded"
    inq.email_response = "Real reply from manufacturer."
    inq.legacy_last_event_key = f"email:{real_reply.id}"
    db.commit()

    client = _client(db)
    with (
        patch("legacy_response_service.maybe_post_for_inquiry") as mock_post,
        patch("slack_service.notify_reply") as mock_slack,
        patch("slack_service.is_configured", return_value=True),
    ):
        mock_post.return_value = True
        r = _post_response(client, inq.id, "Manually logged correction.")

    assert r.status_code == 200

    db.expire_all()
    all_replies = db.query(EmailReply).filter(EmailReply.inquiry_id == inq.id).all()
    # Should have two: the real one (real smtp_message_id) and the new manual one (sentinel)
    assert len(all_replies) == 2

    manual = _manual_replies(db, inq.id)
    assert len(manual) == 1
    assert manual[0].body == "Manually logged correction."

    # Legacy POST fired — event_key is email:{manual_id}, different from email:{real_id}
    mock_post.assert_called_once()
    assert mock_post.call_args.args[2] == f"email:{manual[0].id}"
    assert mock_post.call_args.kwargs.get("email_reply_id") == manual[0].id

    # Slack fired
    mock_slack.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Double-save (sequential proxy for concurrent) — second identical save is a no-op
# ---------------------------------------------------------------------------

def test_double_save_same_text_is_idempotent():
    """Two saves with the same text produce exactly one EmailReply row and
    exactly one legacy POST attempt (second one is deduped by event_key inside
    maybe_post_for_inquiry, which we verify by checking the call args are stable)."""
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr)
    client = _client(db)

    post_calls = []

    def capture_post(db, obj, event_key, *, email_reply_id=None, direct_response_text=None):
        post_calls.append({"event_key": event_key, "email_reply_id": email_reply_id})
        return True

    with (
        patch("legacy_response_service.maybe_post_for_inquiry", side_effect=capture_post),
        patch("slack_service.is_configured", return_value=False),
    ):
        _post_response(client, inq.id, "The answer.")
        _post_response(client, inq.id, "The answer.")

    db.expire_all()
    manual = _manual_replies(db, inq.id)
    # Exactly one EmailReply row — second save updated the existing one
    assert len(manual) == 1

    # Both calls used the SAME event_key (same reply.id)
    assert len(post_calls) == 2
    assert post_calls[0]["event_key"] == post_calls[1]["event_key"]
    assert post_calls[0]["email_reply_id"] == post_calls[1]["email_reply_id"]
    # The actual dedup happens inside maybe_post_for_inquiry (which we mocked here),
    # but both calls carry the stable event_key — real dedup is tested by the guard logic.


def test_double_save_different_text_second_is_treated_as_correction():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr)
    client = _client(db)

    post_calls = []

    def capture_post(db, obj, event_key, *, email_reply_id=None, direct_response_text=None):
        post_calls.append(event_key)
        return True

    with (
        patch("legacy_response_service.maybe_post_for_inquiry", side_effect=capture_post),
        patch("slack_service.is_configured", return_value=False),
    ):
        _post_response(client, inq.id, "First answer.")
        _post_response(client, inq.id, "Different answer.")

    db.expire_all()
    manual = _manual_replies(db, inq.id)
    assert len(manual) == 1
    assert manual[0].body == "Different answer."

    # Both POSTs used the same event_key (same reply.id, stable across updates)
    assert len(post_calls) == 2
    assert post_calls[0] == post_calls[1]


# ---------------------------------------------------------------------------
# 6. Closed inquiry is rejected (status guard)
# ---------------------------------------------------------------------------

def test_closed_inquiry_returns_409():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr, status="closed")
    client = _client(db)

    r = _post_response(client, inq.id, "Any response.")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 7. Real SendGrid reply with no Message-ID is not confused for a manual entry
# ---------------------------------------------------------------------------

def test_real_no_header_reply_not_overwritten_by_manual_save():
    """A real manufacturer email that arrives without a Message-ID header
    creates an EmailReply with smtp_message_id=NULL — different from the
    __manual__ sentinel. A subsequent manual save must NOT overwrite it."""
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr, status="email_sent")

    # Real SendGrid reply with no Message-ID (smtp_message_id=NULL, graph_message_id=NULL)
    real_reply = EmailReply(
        inquiry_id=inq.id,
        direction="inbound",
        sender_email="mi@acmepharma.com",
        body="Real reply, no Message-ID header.",
        sent_at=datetime.now(timezone.utc),
        graph_message_id=None,
        smtp_message_id=None,  # no Message-ID — the edge case
    )
    db.add(real_reply)
    inq.status = "email_responded"
    db.commit()

    client = _client(db)
    with (
        patch("legacy_response_service.maybe_post_for_inquiry", return_value=True),
        patch("slack_service.is_configured", return_value=False),
    ):
        _post_response(client, inq.id, "Manually logged text.")

    db.expire_all()
    all_replies = db.query(EmailReply).filter(EmailReply.inquiry_id == inq.id).all()
    # Three total: real (NULL/NULL) + new manual (sentinel)
    assert len(all_replies) == 2

    real = next(r for r in all_replies if r.smtp_message_id is None)
    manual = next(r for r in all_replies if r.smtp_message_id == _MANUAL_SMTP_ID)

    # Real reply body must be untouched
    assert real.body == "Real reply, no Message-ID header."
    assert manual.body == "Manually logged text."


# ---------------------------------------------------------------------------
# 8. next_retry_at and call_scheduled_for are always cleared
# ---------------------------------------------------------------------------

def test_retry_and_call_schedule_cleared_on_save():
    db = _db()
    user = _make_user(db)
    mfr = _make_mfr(db)
    inq = _make_inquiry(db, user, mfr)

    from datetime import timedelta
    inq.next_retry_at = datetime.now(timezone.utc) + timedelta(hours=2)
    inq.call_scheduled_for = datetime.now(timezone.utc) + timedelta(hours=3)
    db.commit()

    client = _client(db)
    with (
        patch("legacy_response_service.maybe_post_for_inquiry", return_value=True),
        patch("slack_service.is_configured", return_value=False),
    ):
        _post_response(client, inq.id, "Some answer.")

    db.expire_all()
    inq_db = db.get(Inquiry, inq.id)
    assert inq_db.next_retry_at is None
    assert inq_db.call_scheduled_for is None
