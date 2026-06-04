"""Voice agent helpers for the browser-side form assistant.

The form-assistant ElevenLabs agent runs from the frontend via the
@elevenlabs/react SDK. The agent itself is private, so we mint a
short-lived signed URL here using the server's API key instead of
shipping the API key (or making the agent public)."""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException

log = logging.getLogger("inquiry.voice")

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/signed-url")
def get_signed_url(agent_id: Optional[str] = None):
    """Returns a short-lived signed conversation URL for the form-assistant
    ElevenLabs agent. The frontend SDK uses this to start a session without
    needing the API key.

    Pass `agent_id` to override the default (env: ELEVENLABS_FORM_AGENT_ID).
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ELEVENLABS_API_KEY is not configured on the server.",
        )

    agent = agent_id or os.getenv("ELEVENLABS_FORM_AGENT_ID")
    if not agent:
        raise HTTPException(
            status_code=500,
            detail=(
                "No agent_id provided and ELEVENLABS_FORM_AGENT_ID is not set. "
                "Configure the form-assistant agent in ElevenLabs and add its "
                "ID to the server environment."
            ),
        )

    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.get(
                "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                params={"agent_id": agent},
                headers={"xi-api-key": api_key},
            )
            res.raise_for_status()
            data = res.json()
    except httpx.HTTPStatusError as e:
        log.error("ElevenLabs signed-url failed: %s %s", e.response.status_code, e.response.text)
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs rejected signed-url request: {e.response.text}",
        )
    except httpx.HTTPError as e:
        log.error("ElevenLabs signed-url network error: %s", e)
        raise HTTPException(status_code=502, detail="Could not reach ElevenLabs.")

    signed = data.get("signed_url") or data.get("url")
    if not signed:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs returned no signed URL. Response: {data}",
        )
    return {"signed_url": signed, "agent_id": agent}
