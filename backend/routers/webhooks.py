"""Webhook receivers for external services (currently: ElevenLabs post-call)."""
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import Inquiry

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _extract_conversation_id(body: Dict[str, Any]) -> Optional[str]:
    """ElevenLabs sometimes nests under `data` or `conversation` — be lenient."""
    return (
        body.get("conversation_id")
        or body.get("conversationId")
        or (body.get("data") or {}).get("conversation_id")
        or (body.get("conversation") or {}).get("conversation_id")
    )


def _extract_summary(body: Dict[str, Any]) -> Optional[str]:
    return (
        body.get("summary")
        or body.get("call_summary")
        or (body.get("analysis") or {}).get("summary")
        or (body.get("data") or {}).get("summary")
    )


def _extract_transcript(body: Dict[str, Any]) -> Optional[str]:
    # ElevenLabs sends a structured turn list; flatten to plain text for storage
    turns = (
        body.get("transcript")
        or (body.get("data") or {}).get("transcript")
        or body.get("messages")
    )
    if isinstance(turns, str):
        return turns
    if isinstance(turns, list):
        lines = []
        for t in turns:
            if not isinstance(t, dict):
                continue
            role = t.get("role") or t.get("speaker") or "agent"
            text = t.get("message") or t.get("text") or t.get("content") or ""
            if text:
                lines.append(f"{role.upper()}: {text}")
        return "\n".join(lines) if lines else None
    return None


@router.post("/elevenlabs/post-call")
async def elevenlabs_post_call(
    request: Request,
    x_webhook_secret: Optional[str] = Header(None, alias="X-Webhook-Secret"),
    db: Session = Depends(get_db),
):
    """Receives ElevenLabs' post-call payload and writes the result back to
    the matching inquiry by `conversation_id`."""
    secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET")
    if secret and x_webhook_secret != secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    body = await request.json()
    convo_id = _extract_conversation_id(body)
    if not convo_id:
        raise HTTPException(status_code=400, detail="No conversation_id in payload")

    obj = (
        db.query(Inquiry)
        .options(joinedload(Inquiry.manufacturer))
        .filter(Inquiry.call_conversation_id == convo_id)
        .first()
    )
    if not obj:
        # Not one of our inquiries — silently accept so ElevenLabs doesn't retry forever
        return {"matched": False, "conversation_id": convo_id}

    summary = _extract_summary(body)
    transcript = _extract_transcript(body)

    from datetime import datetime, timezone
    obj.status = "call_completed"
    obj.call_completed_at = datetime.now(timezone.utc)
    if summary:
        obj.call_summary = summary
        obj.final_answer = summary
    if transcript:
        obj.call_transcript = transcript
    obj.call_provider_status = body.get("status") or "completed"
    db.commit()
    return {"matched": True, "inquiry_id": obj.id}
