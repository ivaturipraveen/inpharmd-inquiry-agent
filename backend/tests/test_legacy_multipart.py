"""Verify that legacy_response_service builds the correct multipart/form-data payload.

These tests never hit staging or S3. They intercept httpx at the transport layer
and inspect the raw request object that httpx constructs.

Run:
    cd backend && source .venv/bin/activate
    python -m pytest tests/test_legacy_multipart.py -v
"""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest

import legacy_response_service


# ---------------------------------------------------------------------------
# Transport that captures outgoing requests instead of sending them
# ---------------------------------------------------------------------------

class _CapturingTransport(httpx.BaseTransport):
    """Records every outgoing POST so tests can inspect the built payload."""

    def __init__(self, status_codes: list[int] | None = None):
        self._status_codes = list(status_codes or [200])
        self._idx = 0
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()  # materialise the streaming multipart body into memory
        self.requests.append(request)
        code = self._status_codes[min(self._idx, len(self._status_codes) - 1)]
        self._idx += 1
        return httpx.Response(code, json={"ok": True}, request=request)


def _inject_transport(transport: _CapturingTransport):
    """Return a drop-in for httpx.Client that routes through the capturing transport."""
    original = httpx.Client

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return original(transport=transport, **kwargs)

    return factory


# ---------------------------------------------------------------------------
# Multipart body parser (no third-party deps — manual boundary splitting)
# ---------------------------------------------------------------------------

def _parse_parts(request: httpx.Request) -> list[dict]:
    """Parse the multipart body of a captured request.

    Returns a list of dicts: {name, filename, content_type, payload (bytes)}.
    """
    ct = request.headers.get("content-type", "")
    boundary = None
    for segment in ct.split(";"):
        segment = segment.strip()
        if segment.startswith("boundary="):
            boundary = segment[len("boundary="):].strip('"')
            break
    assert boundary, f"No boundary in Content-Type: {ct!r}"

    body = bytes(request.content)
    sep = f"--{boundary}".encode()
    parts = []

    for raw in body.split(sep):
        raw = raw.lstrip(b"\r\n")
        if not raw or raw.startswith(b"--"):
            continue
        if b"\r\n\r\n" not in raw:
            continue
        header_block, payload = raw.split(b"\r\n\r\n", 1)
        payload = payload.rstrip(b"\r\n")

        headers: dict[str, str] = {}
        for line in header_block.split(b"\r\n"):
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.decode().lower().strip()] = v.decode().strip()

        cd = headers.get("content-disposition", "")
        name = filename = None
        for param in cd.split(";"):
            param = param.strip()
            if param.startswith("name="):
                name = param[5:].strip('"')
            elif param.startswith("filename="):
                filename = param[9:].strip('"')

        parts.append({
            "name": name,
            "filename": filename,
            "content_type": headers.get("content-type", "text/plain"),
            "payload": payload,
        })

    return parts


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

_ENV = {"LEGACY_RESPONSE_API_KEY": "test-key"}

_PDF  = ("report.pdf",  b"%PDF-1.4 fake-pdf-bytes",  "application/pdf")
_XLSX = ("export.xlsx", b"PK\x03\x04 fake-xlsx-bytes",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _call(transport, s3_urls, download_results, **overrides):
    defaults = dict(
        inquiry_uuid="uuid-abc-123",
        mfr_email_response="Manufacturer confirmed availability.",
        mfr_attachment=s3_urls,
        manufacturer_name="Pfizer",
        medication_name="Paxlovid",
    )
    defaults.update(overrides)
    with patch("legacy_response_service._download_attachment", side_effect=download_results):
        with patch("legacy_response_service.httpx.Client", _inject_transport(transport)):
            with patch.dict(os.environ, _ENV):
                return legacy_response_service.post_response(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMultipartPayload:

    def test_single_attachment_produces_one_file_part(self):
        transport = _CapturingTransport()
        result = _call(transport, ["https://s3.example.com/report.pdf"], [_PDF])

        assert result is True
        parts = _parse_parts(transport.requests[0])
        attachment_parts = [p for p in parts if p["name"] == "mfr_attachment[]"]
        assert len(attachment_parts) == 1

    def test_two_attachments_produce_two_file_parts(self):
        transport = _CapturingTransport()
        result = _call(
            transport,
            ["https://s3.example.com/report.pdf", "https://s3.example.com/export.xlsx"],
            [_PDF, _XLSX],
        )

        assert result is True
        parts = _parse_parts(transport.requests[0])
        attachment_parts = [p for p in parts if p["name"] == "mfr_attachment[]"]
        assert len(attachment_parts) == 2

    def test_file_part_has_correct_filename_mimetype_and_bytes(self):
        transport = _CapturingTransport()
        _call(transport, ["https://s3.example.com/report.pdf"], [_PDF])

        parts = _parse_parts(transport.requests[0])
        part = next(p for p in parts if p["name"] == "mfr_attachment[]")
        assert part["filename"] == "report.pdf"
        assert part["content_type"] == "application/pdf"
        assert part["payload"] == b"%PDF-1.4 fake-pdf-bytes"

    def test_two_file_parts_have_independent_correct_data(self):
        transport = _CapturingTransport()
        _call(
            transport,
            ["https://s3.example.com/report.pdf", "https://s3.example.com/export.xlsx"],
            [_PDF, _XLSX],
        )

        parts = _parse_parts(transport.requests[0])
        attachment_parts = [p for p in parts if p["name"] == "mfr_attachment[]"]
        by_filename = {p["filename"]: p for p in attachment_parts}

        assert by_filename["report.pdf"]["content_type"] == "application/pdf"
        assert by_filename["report.pdf"]["payload"] == b"%PDF-1.4 fake-pdf-bytes"
        assert by_filename["export.xlsx"]["content_type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert by_filename["export.xlsx"]["payload"] == b"PK\x03\x04 fake-xlsx-bytes"

    def test_text_fields_present_in_multipart(self):
        transport = _CapturingTransport()
        # Use an attachment so the body is multipart (no files → url-encoded)
        _call(transport, ["https://s3.example.com/report.pdf"], [_PDF])

        parts = _parse_parts(transport.requests[0])
        by_name = {p["name"]: p["payload"].decode() for p in parts if p["payload"] is not None}
        assert by_name["inquiry_uuid"] == "uuid-abc-123"
        assert by_name["mfr_email_response"] == "Manufacturer confirmed availability."
        assert by_name["manufacturer_name"] == "Pfizer"
        assert by_name["medication_name"] == "Paxlovid"

    def test_content_type_is_multipart_with_boundary(self):
        transport = _CapturingTransport()
        _call(transport, ["https://s3.example.com/report.pdf"], [_PDF])

        ct = transport.requests[0].headers.get("content-type", "")
        assert ct.startswith("multipart/form-data"), f"Expected multipart, got: {ct!r}"
        assert "boundary=" in ct

    def test_no_mfr_s3_url_fields_present(self):
        transport = _CapturingTransport()
        _call(transport, ["https://s3.example.com/report.pdf"], [_PDF])

        parts = _parse_parts(transport.requests[0])
        names = {p["name"] for p in parts}
        assert "mfr_s3_url[]" not in names
        assert "mfr_s3_url" not in names
        assert "mfr_s3_urls" not in names

    def test_retry_reuses_downloaded_bytes_without_refetching(self):
        """500 on first attempt, 200 on second. _download_attachment called once only."""
        transport = _CapturingTransport(status_codes=[500, 200])
        download_call_count = 0

        def counting_download(url, filename=None):
            nonlocal download_call_count
            download_call_count += 1
            return _PDF

        with patch("legacy_response_service._download_attachment", side_effect=counting_download):
            with patch("legacy_response_service.httpx.Client", _inject_transport(transport)):
                with patch.dict(os.environ, _ENV):
                    result = legacy_response_service.post_response(
                        inquiry_uuid="uuid-retry",
                        mfr_email_response="response text",
                        mfr_attachment=["https://s3.example.com/report.pdf"],
                    )

        assert result is True
        assert len(transport.requests) == 2, "Expected two POST attempts (1 retry)"
        assert download_call_count == 1, "Attachment must be downloaded once, not once per retry"
