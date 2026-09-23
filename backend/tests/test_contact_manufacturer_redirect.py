"""Tests for GET /go/contact-manufacturer/{inquiry_uuid} — the public
redirect InpharmD's "Contact Manufacturer" button links to.

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_contact_manufacturer_redirect.py -v
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from main import app

client = TestClient(app, follow_redirects=False)


def test_redirects_to_contact_manufacturer_hash_with_uuid(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://mfr-stg.inpharmd.com")
    resp = client.get("/go/contact-manufacturer/abc-123-uuid")
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        "https://mfr-stg.inpharmd.com/#contact-manufacturer?uuid=abc-123-uuid"
    )


def test_strips_trailing_slash_from_base_url(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://mfr-stg.inpharmd.com/")
    resp = client.get("/go/contact-manufacturer/abc-123-uuid")
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        "https://mfr-stg.inpharmd.com/#contact-manufacturer?uuid=abc-123-uuid"
    )


def test_fails_clearly_when_app_base_url_unset(monkeypatch):
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    resp = client.get("/go/contact-manufacturer/abc-123-uuid")
    assert resp.status_code == 500
    assert "APP_BASE_URL" in resp.json()["detail"]


def test_url_encodes_special_characters_in_uuid(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://mfr-stg.inpharmd.com")
    # Exercises encoding of characters meaningful in a query string (&, =, space).
    resp = client.get("/go/contact-manufacturer/weird uuid&x=1")
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        "https://mfr-stg.inpharmd.com/#contact-manufacturer?uuid=weird%20uuid%26x%3D1"
    )


def test_no_authentication_required(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://mfr-stg.inpharmd.com")
    # No X-Session-Token header at all — must not 401/403.
    resp = client.get("/go/contact-manufacturer/some-uuid")
    assert resp.status_code == 302


def test_does_not_expose_inquiry_data():
    # The response is a bare redirect — no body carrying inquiry details.
    resp = client.get("/go/contact-manufacturer/some-uuid")
    assert resp.status_code == 302
    assert resp.content in (b"", None) or len(resp.content) < 200
