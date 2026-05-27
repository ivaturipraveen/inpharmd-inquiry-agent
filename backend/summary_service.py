"""LLM-based answer extraction from raw call transcripts.

If the voice agent forgot to call `submit_answer` (or returned no summary),
we can extract a clean clinical answer from the full transcript using OpenAI.

Requires OPENAI_API_KEY env var. Falls back gracefully if not configured.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("inquiry.summary")


class SummaryConfigError(RuntimeError):
    """Raised when OpenAI is not configured or the call fails."""


_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SummaryConfigError(
            "OPENAI_API_KEY not set. Add it to backend/.env to enable AI extraction."
        )
    try:
        from openai import OpenAI
    except ImportError:
        raise SummaryConfigError(
            "openai package not installed. Run: pip install openai>=1.30"
        )
    _client = OpenAI(api_key=api_key)
    return _client


_SYSTEM = (
    "You are extracting the clinical answer from a medical-information phone call transcript. "
    "The call was between an InpharmD inquiry agent (caller) and a pharmaceutical manufacturer's "
    "Medical Information desk (representative). Read the transcript and extract ONLY the clinical "
    "answer the rep provided. Be faithful — do not interpret, do not add caveats unless the rep said "
    "them. If the rep cited a reference (case ID, package insert section, document name, case number), "
    "include it. If no clinical answer was given (voicemail, wrong number, rep declined), say so plainly "
    "in one short sentence."
)


def extract_answer_from_transcript(
    *,
    question: str,
    manufacturer: str,
    transcript: str,
) -> str:
    """Use OpenAI to extract a clean clinical answer from a call transcript."""
    client = _get_client()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    user = (
        f"MANUFACTURER: {manufacturer}\n"
        f"PHARMACIST'S QUESTION: {question}\n\n"
        f"TRANSCRIPT:\n{transcript}\n\n"
        "Extract the rep's clinical answer in 1–3 sentences. Include any references they "
        "cited. If no answer was given, say so plainly."
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=400,
            temperature=0.2,
        )
        answer = (resp.choices[0].message.content or "").strip()
        log.info("Extracted answer (%d chars) via %s", len(answer), model)
        return answer
    except Exception as e:
        log.exception("OpenAI extraction failed: %s", e)
        raise SummaryConfigError(f"OpenAI call failed: {e}")


def is_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))
