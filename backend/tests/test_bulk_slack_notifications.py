"""Tests for the two bulk-email Slack notifications (schedule + completion).

Covers:
  - One schedule notification per bulk batch, listing every scheduled item
    (id, manufacturer, medication, scheduled time) — not one per email.
  - Failed targets (no email on file) are excluded from the batch and the
    notification; a batch where every target fails sends no notification.
  - Completion notification fires exactly once, only once every inquiry in
    the batch has left email_pending.
  - A Slack failure does not mark the batch as notified; it's retried and
    succeeds on the next tick without a duplicate notification.

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_bulk_slack_notifications.py -v
"""
from __future__ import annotations

import os
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

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

from models import BulkEmailBatch, Inquiry, ManufacturerContact, User  # noqa: E402
from main import app  # noqa: E402
from routers.auth import get_current_user  # noqa: E402
import scheduler  # noqa: E402

os.environ.pop("ELEVENLABS_WEBHOOK_SECRET", None)
os.environ.pop("AGENT_TOOLS_SECRET", None)

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_db():
    return TestSession()


def _make_user(db) -> User:
    user = User(email="tester@example.com", session_token="tok", staging_token="stok")
    db.add(user)
    db.flush()
    return user


def _make_manufacturer(db, name: str, *, has_email: bool = True) -> ManufacturerContact:
    mfr = ManufacturerContact(
        manufacturer=name,
        official_mi_email=f"{name.lower()}@example.com" if has_email else None,
    )
    db.add(mfr)
    db.flush()
    return mfr


def _get_test_client(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _clear_overrides():
    app.dependency_overrides.clear()


def _bulk_payload(targets: list[dict]) -> dict:
    return {
        "targets": targets,
        "subject": "Test subject",
        "question": "Test question?",
        "dispatch_channel": "email",
    }


class TestBulkScheduleNotification:
    def test_one_notification_lists_every_scheduled_item(self):
        db = _make_db()
        user = _make_user(db)
        mfrs = [_make_manufacturer(db, f"Mfr{i}") for i in range(3)]
        db.commit()

        client = _get_test_client(db, user)
        try:
            with patch("slack_service.notify_bulk_scheduled") as mock_notify:
                resp = client.post(
                    "/api/inquiries/bulk",
                    json=_bulk_payload([{"manufacturer_id": m.id} for m in mfrs]),
                )
                assert resp.status_code == 201, resp.text
                body = resp.json()
                assert body["dispatched"] == 3

                assert mock_notify.call_count == 1, "must be one notification for the whole batch"
                (batch_id, items), _ = mock_notify.call_args
                assert len(items) == 3
                ids = {i["inquiry_id"] for i in items}
                assert ids == {c["id"] for c in body["created"]}
                for item in items:
                    assert item["manufacturer"] in [m.manufacturer for m in mfrs]
                    assert item["email_scheduled_for"] is not None
        finally:
            _clear_overrides()

        rows = db.query(Inquiry).all()
        batch_ids = {r.bulk_batch_id for r in rows}
        assert len(batch_ids) == 1 and None not in batch_ids
        assert db.query(BulkEmailBatch).count() == 1
        db.close()

    def test_mue_context_passed_when_source_inquiry_uuid_set(self):
        """When the batch was forwarded from a MUE inquiry, the notification
        must carry the shared question + uuid so Slack can show which
        InpharmD inquiry it belongs to (mirrors the "MUE" group row in the
        Outreach tab)."""
        db = _make_db()
        user = _make_user(db)
        mfrs = [_make_manufacturer(db, f"Mfr{i}") for i in range(2)]
        db.commit()

        client = _get_test_client(db, user)
        try:
            with patch("slack_service.notify_bulk_scheduled") as mock_notify:
                payload = _bulk_payload([{"manufacturer_id": m.id} for m in mfrs])
                payload["question"] = "temp excursion api"
                payload["source_inquiry_uuid"] = "mue-uuid-1234"
                resp = client.post("/api/inquiries/bulk", json=payload)
                assert resp.status_code == 201, resp.text

                assert mock_notify.call_count == 1
                _, kwargs = mock_notify.call_args
                assert kwargs["question"] == "temp excursion api"
                assert kwargs["source_inquiry_uuid"] == "mue-uuid-1234"
        finally:
            _clear_overrides()
        db.close()

    def test_no_mue_context_for_manual_batch(self):
        """A manual multi-manufacturer batch has no source_inquiry_uuid —
        the notification must not fabricate a MUE label for it."""
        db = _make_db()
        user = _make_user(db)
        mfrs = [_make_manufacturer(db, f"Mfr{i}") for i in range(2)]
        db.commit()

        client = _get_test_client(db, user)
        try:
            with patch("slack_service.notify_bulk_scheduled") as mock_notify:
                resp = client.post(
                    "/api/inquiries/bulk",
                    json=_bulk_payload([{"manufacturer_id": m.id} for m in mfrs]),
                )
                assert resp.status_code == 201, resp.text
                _, kwargs = mock_notify.call_args
                assert kwargs.get("source_inquiry_uuid") is None
        finally:
            _clear_overrides()
        db.close()

    def test_failed_targets_excluded_from_batch_and_notification(self):
        db = _make_db()
        user = _make_user(db)
        good_a = _make_manufacturer(db, "GoodOne")
        bad = _make_manufacturer(db, "NoEmailMfr", has_email=False)
        good_b = _make_manufacturer(db, "GoodTwo")
        db.commit()

        client = _get_test_client(db, user)
        try:
            with patch("slack_service.notify_bulk_scheduled") as mock_notify:
                resp = client.post(
                    "/api/inquiries/bulk",
                    json=_bulk_payload(
                        [{"manufacturer_id": m.id} for m in (good_a, bad, good_b)]
                    ),
                )
                assert resp.status_code == 201, resp.text
                body = resp.json()
                assert body["dispatched"] == 2
                assert len(body["failed"]) == 1

                assert mock_notify.call_count == 1
                (batch_id, items), _ = mock_notify.call_args
                assert len(items) == 2, "failed target must not appear in the notification"
                names = {i["manufacturer"] for i in items}
                assert names == {"GoodOne", "GoodTwo"}
        finally:
            _clear_overrides()

        bad_inq = db.query(Inquiry).filter(Inquiry.manufacturer_id == bad.id).first()
        assert bad_inq.bulk_batch_id is None
        db.close()

    def test_no_notification_when_every_target_fails(self):
        db = _make_db()
        user = _make_user(db)
        bad = _make_manufacturer(db, "NoEmailMfr", has_email=False)
        db.commit()

        client = _get_test_client(db, user)
        try:
            with patch("slack_service.notify_bulk_scheduled") as mock_notify:
                resp = client.post(
                    "/api/inquiries/bulk",
                    json=_bulk_payload([{"manufacturer_id": bad.id}]),
                )
                assert resp.status_code == 201, resp.text
                assert resp.json()["dispatched"] == 0
                assert mock_notify.call_count == 0
        finally:
            _clear_overrides()

        assert db.query(BulkEmailBatch).count() == 0
        db.close()


def _run_email_send_scan():
    with patch("scheduler.SessionLocal", side_effect=lambda: TestSession()):
        scheduler._notify_completed_bulk_batches()


class TestBulkCompletionNotification:
    def _make_batch(self, db, user, *, statuses: list[str], source_inquiry_uuid: str | None = None) -> str:
        batch_id = "batch-test-1"
        db.add(BulkEmailBatch(batch_id=batch_id))
        for i, status in enumerate(statuses):
            mfr = _make_manufacturer(db, f"Mfr{i}")
            db.add(Inquiry(
                user_id=user.id,
                manufacturer_id=mfr.id,
                subject="s", question="q",
                source_inquiry_uuid=source_inquiry_uuid,
                status=status,
                # email_sent_at is only ever set once an email has actually
                # been sent (scheduler.py), never for draft/email_pending —
                # mirror that invariant here since completion notification
                # now classifies sent-vs-not by this field, not status.
                email_sent_at=_now() if status not in ("draft", "email_pending") else None,
                bulk_batch_id=batch_id,
                max_retries=2,
                fallback_after_hours=0,
            ))
        db.commit()
        return batch_id

    def test_no_notification_while_any_pending(self):
        db = _make_db()
        user = _make_user(db)
        batch_id = self._make_batch(db, user, statuses=["email_sent", "email_pending"])

        with patch("slack_service.notify_bulk_completed") as mock_notify:
            _run_email_send_scan()
            assert mock_notify.call_count == 0

        batch = db.get(BulkEmailBatch, batch_id)
        assert batch.completed_notified_at is None
        db.close()

    def test_completion_notification_carries_mue_context(self):
        db = _make_db()
        user = _make_user(db)
        batch_id = self._make_batch(
            db, user, statuses=["email_sent", "email_sent"], source_inquiry_uuid="mue-uuid-1234",
        )

        with patch("slack_service.notify_bulk_completed", return_value=True) as mock_notify:
            _run_email_send_scan()
            _, kwargs = mock_notify.call_args
            assert kwargs["question"] == "q"
            assert kwargs["source_inquiry_uuid"] == "mue-uuid-1234"
        db.close()

    def test_notification_fires_once_when_all_sent(self):
        db = _make_db()
        user = _make_user(db)
        batch_id = self._make_batch(db, user, statuses=["email_sent", "email_sent"])

        with patch("slack_service.notify_bulk_completed", return_value=True) as mock_notify:
            _run_email_send_scan()
            assert mock_notify.call_count == 1
            _, kwargs = mock_notify.call_args
            assert kwargs["total_count"] == 2
            assert kwargs["sent_count"] == 2
            assert kwargs["cancelled_items"] == []
            assert [i["inquiry_id"] for i in kwargs["sent_items"]] == [1, 2]
            assert [i["manufacturer"] for i in kwargs["sent_items"]] == ["Mfr0", "Mfr1"]
            assert all(i["email_sent_at"] is not None for i in kwargs["sent_items"])

        db.expire_all()
        batch = db.get(BulkEmailBatch, batch_id)
        assert batch.completed_notified_at is not None

        # Second tick must not re-notify.
        with patch("slack_service.notify_bulk_completed") as mock_notify_again:
            _run_email_send_scan()
            assert mock_notify_again.call_count == 0
        db.close()

    def test_cancelled_inquiry_does_not_block_completion_and_is_reported(self):
        """A manually cancelled (draft) inquiry must not block completion
        forever, and must be listed by id/manufacturer/medication in the
        summary rather than silently counted as sent."""
        db = _make_db()
        user = _make_user(db)
        batch_id = "batch-with-cancel"
        db.add(BulkEmailBatch(batch_id=batch_id))
        mfr_sent = _make_manufacturer(db, "SentMfr")
        mfr_cancelled = _make_manufacturer(db, "CancelledMfr")
        db.add(Inquiry(
            user_id=user.id, manufacturer_id=mfr_sent.id,
            subject="s", question="q", status="email_sent",
            email_sent_at=_now(),
            bulk_batch_id=batch_id, max_retries=2, fallback_after_hours=0,
        ))
        cancelled_inq = Inquiry(
            user_id=user.id, manufacturer_id=mfr_cancelled.id,
            subject="s", question="q", status="draft",
            medication_name="Amoxicillin",
            bulk_batch_id=batch_id, max_retries=2, fallback_after_hours=0,
        )
        db.add(cancelled_inq)
        db.commit()

        with patch("slack_service.notify_bulk_completed", return_value=True) as mock_notify:
            _run_email_send_scan()
            assert mock_notify.call_count == 1, "cancelled inquiry must not block completion"
            _, kwargs = mock_notify.call_args
            assert kwargs["total_count"] == 2
            assert kwargs["sent_count"] == 1
            assert len(kwargs["cancelled_items"]) == 1
            item = kwargs["cancelled_items"][0]
            assert item["inquiry_id"] == cancelled_inq.id
            assert item["manufacturer"] == "CancelledMfr"
            assert item["medication_name"] == "Amoxicillin"

        db.expire_all()
        batch = db.get(BulkEmailBatch, batch_id)
        assert batch.completed_notified_at is not None
        db.close()

    def test_closed_before_send_is_reported_cancelled_not_sent(self):
        """Regression: close_inquiry sets status="closed" directly (not
        "draft") and never sets email_sent_at. A batch member closed before
        its scheduled send fired must be reported as not-sent, not counted
        as a delivered email."""
        db = _make_db()
        user = _make_user(db)
        batch_id = "batch-with-early-close"
        mfr_sent = _make_manufacturer(db, "SentMfr")
        mfr_closed = _make_manufacturer(db, "ClosedMfr")
        db.add(Inquiry(
            user_id=user.id, manufacturer_id=mfr_sent.id,
            subject="s", question="q", status="email_sent",
            email_sent_at=_now(),
            bulk_batch_id=batch_id, max_retries=2, fallback_after_hours=0,
        ))
        closed_inq = Inquiry(
            user_id=user.id, manufacturer_id=mfr_closed.id,
            subject="s", question="q", status="closed",
            medication_name="Bendamustine",
            bulk_batch_id=batch_id, max_retries=2, fallback_after_hours=0,
        )
        db.add(closed_inq)
        db.commit()

        with patch("slack_service.notify_bulk_completed", return_value=True) as mock_notify:
            _run_email_send_scan()
            assert mock_notify.call_count == 1
            _, kwargs = mock_notify.call_args
            assert kwargs["sent_count"] == 1
            assert [i["inquiry_id"] for i in kwargs["sent_items"]] == [1]
            assert len(kwargs["cancelled_items"]) == 1
            item = kwargs["cancelled_items"][0]
            assert item["inquiry_id"] == closed_inq.id
            assert item["manufacturer"] == "ClosedMfr"
        db.close()

    def test_missing_batch_row_is_backfilled_and_still_notifies(self):
        """Simulates the BulkEmailBatch insert having failed at schedule time
        (routers/inquiries.py's guarded try/except): inquiries carry a
        bulk_batch_id but no BulkEmailBatch row exists for it. The scheduler
        tick must backfill the row itself rather than stranding the batch
        without a completion notification."""
        db = _make_db()
        user = _make_user(db)
        batch_id = "batch-never-persisted"
        mfr = _make_manufacturer(db, "Mfr")
        db.add(Inquiry(
            user_id=user.id, manufacturer_id=mfr.id,
            subject="s", question="q", status="email_sent",
            email_sent_at=_now(),
            bulk_batch_id=batch_id, max_retries=2, fallback_after_hours=0,
        ))
        db.commit()
        assert db.query(BulkEmailBatch).filter(BulkEmailBatch.batch_id == batch_id).first() is None

        with patch("slack_service.notify_bulk_completed", return_value=True) as mock_notify:
            _run_email_send_scan()
            assert mock_notify.call_count == 1, "backfilled batch must still be checked and notified this tick"

        db.expire_all()
        batch = db.get(BulkEmailBatch, batch_id)
        assert batch is not None, "missing tracking row must be backfilled"
        assert batch.completed_notified_at is not None
        db.close()

    def test_slack_failure_does_not_mark_notified_and_retries_next_tick(self):
        db = _make_db()
        user = _make_user(db)
        batch_id = self._make_batch(db, user, statuses=["email_sent", "email_sent"])

        with patch("slack_service.notify_bulk_completed", return_value=False) as mock_notify:
            _run_email_send_scan()
            assert mock_notify.call_count == 1

        db.expire_all()
        batch = db.get(BulkEmailBatch, batch_id)
        assert batch.completed_notified_at is None, "must not be marked notified on Slack failure"

        # Next tick: Slack recovers, notification succeeds and is marked.
        with patch("slack_service.notify_bulk_completed", return_value=True) as mock_notify2:
            _run_email_send_scan()
            assert mock_notify2.call_count == 1

        db.expire_all()
        batch = db.get(BulkEmailBatch, batch_id)
        assert batch.completed_notified_at is not None
        db.close()


class TestMueContextBlock:
    """Unit tests for the raw block-building helper, independent of the
    scheduler/router plumbing above."""

    def test_renders_mue_line_when_uuid_present(self):
        import slack_service
        block = slack_service._mue_context_block("temp excursion api", "mue-uuid-1234")
        assert block is not None
        assert block["text"]["text"] == "*MUE* — temp excursion api"

    def test_no_block_when_uuid_absent(self):
        import slack_service
        assert slack_service._mue_context_block("some question", None) is None
        assert slack_service._mue_context_block("some question", "") is None

    def test_long_question_is_truncated(self):
        import slack_service
        long_question = "x" * 500
        block = slack_service._mue_context_block(long_question, "mue-uuid-1234")
        assert len(block["text"]["text"]) < 250
        assert block["text"]["text"].endswith("…")
