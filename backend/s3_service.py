"""Object-storage helper. Uploads manufacturer-attached PDFs to AWS S3 and
returns a long-lived presigned URL the team can paste into Slack / email / UI.

Required env vars:
    AWS_ACCESS_KEY_ID       IAM access key with PutObject on the bucket
    AWS_SECRET_ACCESS_KEY   matching secret
    AWS_S3_BUCKET           bucket name
    AWS_S3_REGION           e.g. us-east-1

Optional env vars:
    AWS_S3_PREFIX           key prefix inside the bucket (default: "inquiry-pdfs/")
    AWS_S3_URL_TTL_SECONDS  presigned URL expiry in seconds (default: 604800 = 7d)
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Optional

log = logging.getLogger("inquiry.s3")

_DEFAULT_PREFIX = "inquiry-pdfs/"
_DEFAULT_TTL = 60 * 60 * 24 * 7  # 7 days

# Lazy import — boto3 only loaded when we actually upload, so the rest of the
# app boots fine in environments that don't ship the dependency.
_boto_client = None
_boto_import_err: Optional[Exception] = None


def is_configured() -> bool:
    return bool(
        os.getenv("AWS_S3_BUCKET")
        and os.getenv("AWS_ACCESS_KEY_ID")
        and os.getenv("AWS_SECRET_ACCESS_KEY")
        and os.getenv("AWS_S3_REGION")
    )


def _client():
    global _boto_client, _boto_import_err
    if _boto_client is not None:
        return _boto_client
    try:
        import boto3  # type: ignore
    except Exception as e:
        _boto_import_err = e
        raise RuntimeError(
            "boto3 not installed; add `boto3` to backend/requirements.txt"
        ) from e
    _boto_client = boto3.client(
        "s3",
        region_name=os.getenv("AWS_S3_REGION"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    )
    return _boto_client


def _safe_filename(name: str) -> str:
    # Keep the original-ish filename for human readability but strip path
    # separators and anything that would break a URL.
    base = (name or "attachment").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Drop any query string left over from a presigned URL.
    base = base.split("?", 1)[0]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")
    return base or "attachment"


def upload_pdf(content: bytes, *, original_name: str, inquiry_id: int) -> Optional[str]:
    """Upload PDF bytes; return a presigned GET URL (or None if not configured)."""
    return upload_bytes(
        content,
        original_name=original_name or "attachment.pdf",
        inquiry_id=inquiry_id,
        content_type="application/pdf",
    )


def upload_bytes(
    content: bytes,
    *,
    original_name: str,
    inquiry_id: int,
    content_type: str = "application/octet-stream",
    prefix: Optional[str] = None,
    key_override: Optional[str] = None,
) -> Optional[str]:
    """Generic upload helper. Returns a presigned GET URL (or None if not
    configured). Used by both the PDF and Excel writeback paths.

    Pass `key_override` to write to an exact S3 key — overwrites any existing
    object at that key. Used by the MUE Excel writeback so all replies for one
    MUE inquiry land on a single deterministic key instead of accreting copies.
    """
    if not is_configured():
        log.info("S3 not configured; skipping upload for inquiry %s", inquiry_id)
        return None
    bucket = os.environ["AWS_S3_BUCKET"]
    safe = _safe_filename(original_name)
    if key_override:
        key = key_override.strip("/")
    else:
        use_prefix = (prefix or os.getenv("AWS_S3_PREFIX", _DEFAULT_PREFIX)).strip("/")
        key = f"{use_prefix}/inquiry-{inquiry_id}/{uuid.uuid4().hex[:10]}-{safe}"
    try:
        client = _client()
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            ContentDisposition=f'inline; filename="{safe}"',
        )
        ttl = int(os.getenv("AWS_S3_URL_TTL_SECONDS", str(_DEFAULT_TTL)))
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl,
        )
        log.info("Uploaded %s for inquiry %s to s3://%s/%s", content_type, inquiry_id, bucket, key)
        return url
    except Exception as e:
        log.exception("S3 upload failed for inquiry %s: %s", inquiry_id, e)
        return None
