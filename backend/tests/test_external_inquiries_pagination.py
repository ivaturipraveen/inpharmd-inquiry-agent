"""Tests for the two-path GET /api/external/inquiries design:

  - Unfiltered browsing (no search/inquiry_type/with_attachments) fetches
    exactly the requested page directly from staging — no cache, no
    5000-record full fetch, no X-Cache headers.
  - Any search/inquiry_type/with_attachments filter uses the pre-existing
    full-fetch-and-cache path, completely unchanged: same cache key, same
    TTL, same X-Cache headers, same in-process filtering.
  - The two paths never share a cache; fresh only affects the filtered path.

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_external_inquiries_pagination.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
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

from models import User  # noqa: E402
from main import app  # noqa: E402
from routers.auth import get_current_user  # noqa: E402
import cache_service  # noqa: E402
import inpharmd_service  # noqa: E402
from routers import external_inquiries  # noqa: E402

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_state():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    cache_service.invalidate_prefix("external:")
    yield
    cache_service.invalidate_prefix("external:")


def _make_db():
    return TestSession()


def _make_user(db, *, email="tester@example.com", staging_token="stok") -> User:
    user = User(email=email, session_token=f"tok-{email}", staging_token=staging_token)
    db.add(user)
    db.flush()
    return user


def _get_test_client(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _clear_overrides():
    app.dependency_overrides.clear()


def _staging_page(page, per_page, total_entries, item_prefix="mue"):
    """Mimics the real staging response shape (verified against the Rails
    source: {data: [...], meta: {page, per_page, total_entries, total_pages}})."""
    total_pages = max(1, -(-total_entries // per_page))
    start = (page - 1) * per_page
    n_this_page = max(0, min(per_page, total_entries - start))
    items = [
        {"inquiry_uuid": f"{item_prefix}-{start + i}", "title": f"Inquiry {start + i}",
         "inquiry_submitter": "Someone", "project_types": "none", "temperature_excursion": False,
         "attachments": [], "inquiry_submitter_details": {}}
        for i in range(n_this_page)
    ]
    return {"data": items, "meta": {"page": page, "per_page": per_page,
                                     "total_entries": total_entries, "total_pages": total_pages}}


class TestUnfilteredPath:
    def test_page_1_fetches_only_requested_page_from_staging(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(1, 20, 1922)
                resp = client.get("/api/external/inquiries?page=1&per_page=20")
                assert resp.status_code == 200, resp.text
                assert mock_list.call_count == 1
                _, kwargs = mock_list.call_args
                assert kwargs["page"] == 1
                assert kwargs["per_page"] == 20
        finally:
            _clear_overrides()
        db.close()

    def test_uses_lightweight_timeout_and_retry_policy(self):
        """Unfiltered page fetches must use the shorter, page-specific
        timeout/retry policy — not the 60s/2-retry policy sized for the
        heavy 5000-record full fetch (that would tie up a backend thread
        for up to ~184s on every single page click during a staging
        slowdown, since this path has no cache to absorb repeat hits)."""
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(1, 20, 5)
                client.get("/api/external/inquiries?page=1")
                _, kwargs = mock_list.call_args
                assert kwargs["timeout"] == inpharmd_service.PAGE_LIST_TIMEOUT_SECONDS
                assert kwargs["max_retries"] == inpharmd_service.PAGE_LIST_MAX_RETRIES
                # And that policy must actually be lighter than the full-fetch one.
                assert inpharmd_service.PAGE_LIST_TIMEOUT_SECONDS < inpharmd_service.LIST_TIMEOUT_SECONDS
                assert inpharmd_service.PAGE_LIST_MAX_RETRIES < inpharmd_service.MAX_RETRIES
        finally:
            _clear_overrides()
        db.close()

    def test_page_5_fetches_only_that_page_not_everything(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(5, 20, 1922)
                resp = client.get("/api/external/inquiries?page=5&per_page=20")
                assert resp.status_code == 200, resp.text
                _, kwargs = mock_list.call_args
                assert kwargs["page"] == 5
                assert kwargs["per_page"] == 20
                # Never the full-fetch ceiling used by the filtered path.
                assert kwargs["per_page"] != external_inquiries._FULL_FETCH_PER_PAGE
        finally:
            _clear_overrides()
        db.close()

    def test_correct_staging_pagination_metadata_returned(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(2, 20, 1922)
                resp = client.get("/api/external/inquiries?page=2&per_page=20")
                body = resp.json()
                assert body["meta"] == {"page": 2, "per_page": 20, "total_entries": 1922, "total_pages": 97}
                assert len(body["data"]) == 20
        finally:
            _clear_overrides()
        db.close()

    def test_no_cache_headers_on_unfiltered_path(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(1, 20, 5)
                resp = client.get("/api/external/inquiries?page=1")
                assert "X-Cache" not in resp.headers
                assert "X-Cache-Age" not in resp.headers
        finally:
            _clear_overrides()
        db.close()

    def test_unfiltered_path_never_populates_full_list_cache(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(1, 20, 5)
                client.get("/api/external/inquiries?page=1")
                key = external_inquiries._full_list_cache_key(user.id)
                assert cache_service.get(key) is None
                assert cache_service.get_stale_ok(key) is None
        finally:
            _clear_overrides()
        db.close()

    def test_fresh_has_no_effect_on_unfiltered_path(self):
        """fresh only means anything where there's a cache to invalidate.
        On the unfiltered path there is none — passing it must not error,
        must not trigger a full 5000-record fetch, and must not populate
        the full-list cache."""
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(1, 20, 5)
                resp = client.get("/api/external/inquiries?page=1&fresh=true")
                assert resp.status_code == 200, resp.text
                _, kwargs = mock_list.call_args
                assert kwargs["per_page"] == 20
                key = external_inquiries._full_list_cache_key(user.id)
                assert cache_service.get(key) is None
        finally:
            _clear_overrides()
        db.close()

    def test_out_of_range_page_is_clamped_in_reported_meta(self):
        """Staging echoes back the requested page verbatim, unclamped, even
        when it's beyond total_pages (verified against the Rails source —
        .paginate() doesn't reject or adjust an out-of-range page). If the
        live dataset shrank since the page was chosen (this path has no
        stable snapshot — every page is a fresh query), our own response
        must still report a valid page so the frontend's meta.page-based
        sync can self-correct instead of leaving the pager stuck on a page
        that no longer exists."""
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                # Staging returns page=97 verbatim (as requested) but the
                # dataset has shrunk to only 96 pages, and no rows for 97.
                mock_list.return_value = {
                    "data": [],
                    "meta": {"page": 97, "per_page": 20, "total_entries": 1920, "total_pages": 96},
                }
                resp = client.get("/api/external/inquiries?page=97")
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["meta"]["page"] == 96  # clamped, not the raw 97 staging echoed
                assert body["meta"]["total_pages"] == 96
        finally:
            _clear_overrides()
        db.close()

    def test_in_range_page_is_not_altered(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(3, 20, 1922)
                resp = client.get("/api/external/inquiries?page=3")
                assert resp.json()["meta"]["page"] == 3
        finally:
            _clear_overrides()
        db.close()

    def test_upstream_401_maps_to_401(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.side_effect = inpharmd_service.InpharmdAPIError(401, "expired")
                resp = client.get("/api/external/inquiries?page=1")
                assert resp.status_code == 401
        finally:
            _clear_overrides()
        db.close()

    def test_upstream_error_maps_to_502(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.side_effect = inpharmd_service.InpharmdAPIError(503, "down")
                resp = client.get("/api/external/inquiries?page=1")
                assert resp.status_code == 502
        finally:
            _clear_overrides()
        db.close()


class TestFilteredPathUnchanged:
    FULL_DATASET = {
        "data": [
            {"inquiry_uuid": "a1", "title": "Packaging Configuration", "inquiry_submitter": "Alice",
             "project_types": "none", "temperature_excursion": False, "attachments": [],
             "inquiry_submitter_details": {"email": "alice@example.com"}},
            {"inquiry_uuid": "a2", "title": "Storage Question", "inquiry_submitter": "Bob",
             "project_types": "PT Review", "temperature_excursion": False,
             "attachments": [{"file_name": "sheet.xlsx"}],
             "inquiry_submitter_details": {"email": "bob@example.com"}},
            {"inquiry_uuid": "a3", "title": "Temp Excursion Report", "inquiry_submitter": "Carol",
             "project_types": "none", "temperature_excursion": True, "attachments": [],
             "inquiry_submitter_details": {"email": "carol@example.com"}},
        ],
        "meta": {"page": 1, "per_page": 5000, "total_entries": 3, "total_pages": 1},
    }

    def test_search_only_uses_full_fetch_and_cache_path(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                resp = client.get("/api/external/inquiries?search=packaging")
                assert resp.status_code == 200, resp.text
                _, kwargs = mock_list.call_args
                assert kwargs["per_page"] == external_inquiries._FULL_FETCH_PER_PAGE
                assert resp.headers["X-Cache"] == "MISS"
                body = resp.json()
                assert len(body["data"]) == 1
                assert body["data"][0]["inquiry_uuid"] == "a1"
                key = external_inquiries._full_list_cache_key(user.id)
                assert cache_service.get(key) is not None
        finally:
            _clear_overrides()
        db.close()

    def test_filtered_path_does_not_override_timeout_or_retries(self):
        """The full-fetch path must keep inheriting list_inquiries' defaults
        (LIST_TIMEOUT_SECONDS/MAX_RETRIES) — only the new unfiltered-page
        path passes an explicit lighter policy. Asserting the kwargs are
        entirely absent (not just equal to the default) proves this call
        site itself was not touched by the timeout/retry change."""
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                client.get("/api/external/inquiries?search=packaging")
                _, kwargs = mock_list.call_args
                assert "timeout" not in kwargs
                assert "max_retries" not in kwargs
        finally:
            _clear_overrides()
        db.close()

    def test_inquiry_type_only_uses_full_fetch_and_cache_path(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                resp = client.get("/api/external/inquiries?inquiry_type=TE")
                assert resp.status_code == 200, resp.text
                assert mock_list.call_args.kwargs["per_page"] == external_inquiries._FULL_FETCH_PER_PAGE
                body = resp.json()
                assert len(body["data"]) == 1
                assert body["data"][0]["inquiry_uuid"] == "a3"
                assert resp.headers["X-Cache"] == "MISS"
        finally:
            _clear_overrides()
        db.close()

    def test_with_attachments_only_uses_full_fetch_and_cache_path(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                resp = client.get("/api/external/inquiries?with_attachments=true")
                assert resp.status_code == 200, resp.text
                assert mock_list.call_args.kwargs["per_page"] == external_inquiries._FULL_FETCH_PER_PAGE
                body = resp.json()
                assert len(body["data"]) == 1
                assert body["data"][0]["inquiry_uuid"] == "a2"
        finally:
            _clear_overrides()
        db.close()

    def test_combined_filters_behave_correctly(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                # search "storage" AND with_attachments=true -> only a2 matches both.
                resp = client.get("/api/external/inquiries?search=storage&with_attachments=true")
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert len(body["data"]) == 1
                assert body["data"][0]["inquiry_uuid"] == "a2"

                # search "storage" AND inquiry_type=TE -> no item matches both.
                resp2 = client.get("/api/external/inquiries?search=storage&inquiry_type=TE")
                assert len(resp2.json()["data"]) == 0
        finally:
            _clear_overrides()
        db.close()

    def test_second_call_with_same_filter_is_cache_hit(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                r1 = client.get("/api/external/inquiries?search=storage")
                assert r1.headers["X-Cache"] == "MISS"
                r2 = client.get("/api/external/inquiries?inquiry_type=TE")  # different filter, same cached full list
                assert r2.headers["X-Cache"] == "HIT"
                assert mock_list.call_count == 1  # staging only ever called once
        finally:
            _clear_overrides()
        db.close()

    def test_fresh_invalidates_cache_on_filtered_path(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                client.get("/api/external/inquiries?search=storage")
                assert mock_list.call_count == 1
                resp = client.get("/api/external/inquiries?search=storage&fresh=true")
                assert resp.headers["X-Cache"] == "MISS"
                assert mock_list.call_count == 2
        finally:
            _clear_overrides()
        db.close()

    def test_upstream_error_falls_back_to_stale_cache(self):
        """Pre-existing behavior (unchanged by this work): stale fallback
        only applies when the cache entry is present-but-expired at the time
        of the failed fetch. fresh=true explicitly deletes the entry first
        (_fetch_full_list's `if fresh: cache_service.invalidate(key)`), so a
        fresh=true request that then fails has nothing to fall back to —
        verified separately below, not asserted as a bug to fix here."""
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                client.get("/api/external/inquiries?search=storage")  # warms cache, fresh

                # Age the entry past its TTL without waiting in real time.
                key = external_inquiries._full_list_cache_key(user.id)
                cache_service._store[key].ttl_seconds = 0

                mock_list.side_effect = inpharmd_service.InpharmdAPIError(503, "down")
                resp = client.get("/api/external/inquiries?search=storage")  # no fresh=true
                assert resp.status_code == 200, resp.text
                assert resp.headers["X-Cache"] == "STALE"
                assert "X-Upstream-Error" in resp.headers
        finally:
            _clear_overrides()
        db.close()

    def test_fresh_true_plus_upstream_failure_has_no_stale_to_fall_back_to(self):
        """fresh=true deletes the cache entry before attempting the fetch, so
        if that fetch then fails, there is nothing left to fall back to —
        this is existing, unchanged behavior, verified explicitly here."""
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = self.FULL_DATASET
                client.get("/api/external/inquiries?search=storage")  # warms cache

                mock_list.side_effect = inpharmd_service.InpharmdAPIError(503, "down")
                resp = client.get("/api/external/inquiries?search=storage&fresh=true")
                assert resp.status_code == 502
        finally:
            _clear_overrides()
        db.close()


class TestClearingFiltersReturnsToLivePath:
    def test_clearing_filters_goes_back_to_unfiltered_page_fetch(self):
        db = _make_db()
        user = _make_user(db)
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = TestFilteredPathUnchanged.FULL_DATASET
                r1 = client.get("/api/external/inquiries?search=storage")
                assert "X-Cache" in r1.headers  # filtered path

                mock_list.return_value = _staging_page(1, 20, 3)
                r2 = client.get("/api/external/inquiries?page=1")  # filters cleared
                assert "X-Cache" not in r2.headers  # unfiltered path — no cache headers
                # Confirms it went to staging live for page 1, not the stale filtered cache.
                assert mock_list.call_args.kwargs["per_page"] == 20
        finally:
            _clear_overrides()
        db.close()


class TestUserIsolation:
    def test_different_users_get_independent_full_list_caches(self):
        db = _make_db()
        user_a = _make_user(db, email="a@example.com", staging_token="tok-a")
        user_b = _make_user(db, email="b@example.com", staging_token="tok-b")

        client_a = _get_test_client(db, user_a)
        with patch("inpharmd_service.list_inquiries") as mock_list:
            mock_list.return_value = TestFilteredPathUnchanged.FULL_DATASET
            resp_a = client_a.get("/api/external/inquiries?search=storage")
            assert resp_a.headers["X-Cache"] == "MISS"
        _clear_overrides()

        client_b = _get_test_client(db, user_b)
        with patch("inpharmd_service.list_inquiries") as mock_list:
            mock_list.return_value = TestFilteredPathUnchanged.FULL_DATASET
            # user B must not see user A's cached full list — expect a fresh MISS, not a HIT.
            resp_b = client_b.get("/api/external/inquiries?search=storage")
            assert resp_b.headers["X-Cache"] == "MISS"
            assert mock_list.call_count == 1
        _clear_overrides()
        db.close()

    def test_unfiltered_path_uses_the_requesting_users_own_token(self):
        db = _make_db()
        user = _make_user(db, email="c@example.com", staging_token="unique-token-c")
        client = _get_test_client(db, user)
        try:
            with patch("inpharmd_service.list_inquiries") as mock_list:
                mock_list.return_value = _staging_page(1, 20, 1)
                client.get("/api/external/inquiries?page=1")
                args, _ = mock_list.call_args
                assert args[0] == "unique-token-c"
        finally:
            _clear_overrides()
        db.close()

    def test_unauthenticated_request_rejected(self):
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)
        try:
            resp = client.get("/api/external/inquiries?page=1")
            assert resp.status_code in (401, 403, 422)
        finally:
            _clear_overrides()
        db.close()
