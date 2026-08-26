"""Tests that inpharmd_service._call()'s max_retries/timeout parameters
actually control real retry behavior end-to-end — not just that the right
kwargs get passed through a mock (see test_external_inquiries_pagination.py
for that layer). Mocks httpx.Client itself so no real network call is made.

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_inpharmd_service_retry_policy.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import inpharmd_service


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.reason_phrase = "Service Unavailable"
        self.text = "service unavailable"

    def json(self):
        return {"error": "service unavailable"}


class _FakeClient:
    """Mimics httpx.Client's context-manager protocol; every .request() call
    returns a retryable 503 so we can count real attempts."""
    call_count = 0

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, *a, **k):
        _FakeClient.call_count += 1
        return _FakeResponse(503)


def _reset():
    _FakeClient.call_count = 0


class TestMaxRetriesControlsRealAttemptCount:
    def test_default_max_retries_makes_three_total_attempts(self):
        _reset()
        with patch("httpx.Client", _FakeClient):
            try:
                inpharmd_service._call("GET", "/x", timeout=1.0)
            except inpharmd_service.InpharmdAPIError:
                pass
        assert _FakeClient.call_count == inpharmd_service.MAX_RETRIES + 1 == 3

    def test_page_list_max_retries_makes_two_total_attempts(self):
        _reset()
        with patch("httpx.Client", _FakeClient):
            try:
                inpharmd_service._call(
                    "GET", "/x", timeout=1.0,
                    max_retries=inpharmd_service.PAGE_LIST_MAX_RETRIES,
                )
            except inpharmd_service.InpharmdAPIError:
                pass
        assert _FakeClient.call_count == inpharmd_service.PAGE_LIST_MAX_RETRIES + 1 == 2

    def test_zero_max_retries_makes_exactly_one_attempt(self):
        _reset()
        with patch("httpx.Client", _FakeClient):
            try:
                inpharmd_service._call("GET", "/x", timeout=1.0, max_retries=0)
            except inpharmd_service.InpharmdAPIError:
                pass
        assert _FakeClient.call_count == 1

    def test_list_inquiries_default_matches_full_fetch_policy(self):
        """No override passed → list_inquiries must retry exactly as many
        times as it always has (MAX_RETRIES), proving the full-fetch path's
        behavior is unchanged by this work."""
        _reset()
        with patch("httpx.Client", _FakeClient):
            try:
                inpharmd_service.list_inquiries("tok", page=1, per_page=5000)
            except inpharmd_service.InpharmdAPIError:
                pass
        assert _FakeClient.call_count == inpharmd_service.MAX_RETRIES + 1

    def test_list_inquiries_with_page_policy_matches_lightweight_retries(self):
        _reset()
        with patch("httpx.Client", _FakeClient):
            try:
                inpharmd_service.list_inquiries(
                    "tok", page=1, per_page=20,
                    timeout=inpharmd_service.PAGE_LIST_TIMEOUT_SECONDS,
                    max_retries=inpharmd_service.PAGE_LIST_MAX_RETRIES,
                )
            except inpharmd_service.InpharmdAPIError:
                pass
        assert _FakeClient.call_count == inpharmd_service.PAGE_LIST_MAX_RETRIES + 1


class TestTimeoutPassedToHttpxClient:
    def test_page_list_timeout_reaches_httpx_client(self):
        _reset()
        seen_timeouts = []
        real_init = _FakeClient.__init__

        def capturing_init(self, *a, **k):
            seen_timeouts.append(k.get("timeout"))
            real_init(self, *a, **k)

        with patch("httpx.Client", _FakeClient), patch.object(_FakeClient, "__init__", capturing_init):
            try:
                inpharmd_service._call(
                    "GET", "/x",
                    timeout=inpharmd_service.PAGE_LIST_TIMEOUT_SECONDS,
                    max_retries=0,
                )
            except inpharmd_service.InpharmdAPIError:
                pass
        assert seen_timeouts == [inpharmd_service.PAGE_LIST_TIMEOUT_SECONDS]
