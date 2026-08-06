"""Shared inbound-attachment processing used by all three reply-capture paths
(Graph poller, IMAP poller, SendGrid webhook).

Responsibilities:
    - filter attachments to supported document types
    - upload bytes to S3/R2
    - generate per-document GPT summaries when summary_service is configured
    - create InquiryAttachment rows in the database

Not in scope:
    - deduplication (callers handle that before calling this)
    - updating Inquiry scalar fields (callers do that using the returned list)
    - legacy_response_service, Slack, email-body GPT extraction
"""
from __future__ import annotations

import logging
from typing import Optional

import s3_service
import summary_service
from models import InquiryAttachment

log = logging.getLogger("inquiry.inbound_attachments")

# Supported document extensions and their canonical MIME types.
_SUPPORTED_EXTENSIONS = {
    ".pdf":  "application/pdf",
    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls":  "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv":  "text/csv",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}
_SUPPORTED_CONTENT_TYPES = set(_SUPPORTED_EXTENSIONS.values()) | {"application/csv"}

# Image types are stored and displayed like any other attachment but are never
# summarized — no OCR or GPT vision is attempted.
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

MAX_ATTACHMENTS = 20


def is_supported(filename: str, content_type: str) -> bool:
    """Return True if the file extension or content-type is in the supported set."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext in _SUPPORTED_EXTENSIONS:
        return True
    ct = (content_type or "").lower()
    return any(ct.startswith(k) for k in _SUPPORTED_CONTENT_TYPES)


def process_attachments(
    db,
    inquiry_id: int,
    reply_id: int,
    raw_attachments: list[dict],
    question: str,
    manufacturer_name: str,
) -> list[dict]:
    """Upload, summarise, and persist inbound attachments.

    Args:
        db:                 SQLAlchemy session (caller commits).
        inquiry_id:         ID of the parent Inquiry.
        reply_id:           ID of the EmailReply row these attachments belong to.
        raw_attachments:    List of dicts: {bytes, name, content_type}.
                            Caller is responsible for collecting these from the
                            transport (Graph API, IMAP, SendGrid form).
        question:           Inquiry question text (used for GPT document summary).
        manufacturer_name:  Manufacturer name (used for GPT document summary).

    Returns:
        List of dicts for each successfully uploaded attachment:
            {url, filename, content_type, summary, display_order}
        The list preserves input order. Attachments that fail S3 upload are
        omitted from the return value but logged.
    """
    uploaded: list[dict] = []
    order = 0

    for raw in raw_attachments:
        if order >= MAX_ATTACHMENTS:
            log.warning(
                "Inquiry %s: reached attachment cap (%d); skipping remaining",
                inquiry_id, MAX_ATTACHMENTS,
            )
            break

        name: str = (raw.get("name") or "attachment").strip()[:512]
        content_type: str = raw.get("content_type") or "application/octet-stream"
        data: bytes = raw.get("bytes") or b""

        if not data:
            continue
        if not is_supported(name, content_type):
            log.info(
                "Inquiry %s attachment %d '%s' (%s) is not a supported type; skipping",
                inquiry_id, order, name, content_type,
            )
            continue

        try:
            url: Optional[str] = s3_service.upload_bytes(
                data,
                original_name=name,
                inquiry_id=inquiry_id,
                content_type=content_type,
            )
        except Exception as exc:
            log.warning(
                "S3 upload raised for inquiry %s attachment %d '%s': %s; skipping",
                inquiry_id, order, name, exc,
            )
            continue
        if url is None:
            log.warning(
                "S3 upload returned None for inquiry %s attachment %d '%s'; skipping",
                inquiry_id, order, name,
            )
            continue

        att_summary: Optional[str] = None
        file_ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if file_ext not in _IMAGE_EXTENSIONS and summary_service.is_configured():
            doc_text = summary_service.extract_document_text(name, data)
            if doc_text:
                try:
                    att_summary = summary_service.summarize_pdf(
                        question=question,
                        manufacturer=manufacturer_name,
                        pdf_text=doc_text,
                    )
                except Exception as exc:
                    log.warning(
                        "Document summary unavailable for inquiry %s attachment %d '%s': %s",
                        inquiry_id, order, name, exc,
                    )

        db.add(InquiryAttachment(
            inquiry_id=inquiry_id,
            reply_id=reply_id,
            url=url,
            filename=name,
            content_type=content_type,
            summary=att_summary,
            display_order=order,
        ))
        log.info(
            "Inquiry %s attachment %d '%s' (%d bytes) uploaded to %s",
            inquiry_id, order, name, len(data), url,
        )
        uploaded.append({
            "url": url,
            "filename": name,
            "content_type": content_type,
            "summary": att_summary,
            "display_order": order,
        })
        order += 1

    return uploaded
