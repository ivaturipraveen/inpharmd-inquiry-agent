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


_MODEL = "gpt-4o-mini"


def extract_answer_from_transcript(
    *,
    question: str,
    manufacturer: str,
    transcript: str,
) -> str:
    """Use OpenAI to extract a clean clinical answer from a call transcript."""
    client = _get_client()

    user = (
        f"MANUFACTURER: {manufacturer}\n"
        f"PHARMACIST'S QUESTION: {question}\n\n"
        f"TRANSCRIPT:\n{transcript}\n\n"
        "Extract the rep's clinical answer in 1–3 sentences. Include any references they "
        "cited. If no answer was given, say so plainly."
    )

    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=400,
            temperature=0.2,
        )
        answer = (resp.choices[0].message.content or "").strip()
        log.info("Extracted answer (%d chars) via %s", len(answer), _MODEL)
        return answer
    except Exception as e:
        log.exception("OpenAI extraction failed: %s", e)
        raise SummaryConfigError(f"OpenAI call failed: {e}")


_EMAIL_SYSTEM = (
    "You are extracting the clinical answer from a medical-information email reply. "
    "The reply is from a pharmaceutical manufacturer's Medical Information desk, answering "
    "a pharmacist's clinical question that InpharmD forwarded. Read the reply and extract ONLY "
    "the clinical answer the manufacturer provided. Be faithful — do not interpret, do not add "
    "caveats unless they wrote them. If they cited a reference (case ID, package insert section, "
    "document name, case number), include it. Ignore email signatures, disclaimers, legal "
    "boilerplate, and quoted text from earlier messages. If no clinical answer was given "
    "(auto-reply, acknowledgement only, request for more info, declined), say so plainly in one short sentence."
)


def extract_answer_from_email(
    *,
    question: str,
    manufacturer: str,
    reply_text: str,
) -> str:
    """Use OpenAI to extract a clean clinical answer from an email reply body."""
    client = _get_client()

    user = (
        f"MANUFACTURER: {manufacturer}\n"
        f"PHARMACIST'S QUESTION: {question}\n\n"
        f"MANUFACTURER'S EMAIL REPLY:\n{reply_text}\n\n"
        "Extract their clinical answer in 1–3 sentences. Include any references they "
        "cited. If no answer was given, say so plainly."
    )

    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _EMAIL_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=400,
            temperature=0.2,
        )
        answer = (resp.choices[0].message.content or "").strip()
        log.info("Extracted email answer (%d chars) via %s", len(answer), _MODEL)
        return answer
    except Exception as e:
        log.exception("OpenAI email extraction failed: %s", e)
        raise SummaryConfigError(f"OpenAI call failed: {e}")


_SIG_STRIP_SYSTEM = (
    "You receive the plain-text body of a manufacturer's email reply. Your ONLY job is to "
    "return the same body with the email signature, disclaimer, and confidentiality notice "
    "removed. Preserve the answer text VERBATIM — do not paraphrase, summarize, reword, "
    "fix grammar, add caveats, or change formatting in any way. Do not add a greeting, a "
    "closing, or any extra text. Return only the cleaned body. If the body has no signature "
    "to remove, return it exactly as received."
)


def strip_signature_with_ai(reply_text: str) -> str:
    """Use OpenAI to strip an email signature when regex-based stripping leaves
    one behind. The model is instructed to preserve the answer text verbatim
    and only remove the signature/disclaimer block. Falls back to the input
    on any error so we never lose the reply."""
    text = (reply_text or "").strip()
    if not text:
        return text
    try:
        client = _get_client()
    except SummaryConfigError:
        return text

    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SIG_STRIP_SYSTEM},
                {"role": "user", "content": text},
            ],
            max_tokens=1200,
            temperature=0.0,
        )
        cleaned = (resp.choices[0].message.content or "").strip()
        if not cleaned:
            return text
        # Safety: if the model returned something dramatically shorter than the
        # input, it probably over-stripped — keep the manual version instead.
        if len(cleaned) < max(40, int(len(text) * 0.20)):
            log.warning("AI signature strip returned too little; keeping manual version")
            return text
        log.info("AI signature strip: %d → %d chars", len(text), len(cleaned))
        return cleaned
    except Exception as e:
        log.warning("AI signature strip failed (%s); keeping manual version", e)
        return text


def is_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))
