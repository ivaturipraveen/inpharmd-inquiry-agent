"""ElevenLabs Twilio outbound-call wrapper for manufacturer MI inquiries.

The endpoint we hit is documented at:
    https://elevenlabs.io/docs/conversational-ai/api-reference/twilio/outbound-call

We push the inquiry context into `conversation_initiation_client_data` so the
agent's first message and dynamic variables are inquiry-specific without
needing a custom agent per inquiry.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import httpx

ELEVEN_BASE = "https://api.elevenlabs.io/v1"


class CallConfigError(RuntimeError):
    """Raised when ElevenLabs / Twilio configuration is missing."""


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise CallConfigError(
            f"Environment variable {name} is not set. "
            "Add it to backend/.env to enable outbound calls."
        )
    return val


def _agent_id() -> str:
    # Prefer a dedicated inquiry agent; fall back to the personal-assistant agent
    return (
        os.getenv("ELEVENLABS_INQUIRY_AGENT_ID")
        or _required("ELEVENLABS_AGENT_ID")
    )


def _phone_number_id() -> str:
    return (
        os.getenv("ELEVENLABS_INQUIRY_PHONE_NUMBER_ID")
        or _required("ELEVENLABS_AGENT_PHONE_NUMBER_ID")
    )


# ---------- Business-hours parsing ----------

@dataclass
class HoursWindow:
    weekdays: set[int]          # 0=Mon ... 6=Sun
    open_local: Optional[time]  # None = unknown / 24h
    close_local: Optional[time]
    tz: ZoneInfo

_TZ_MAP = {
    "ET": "America/New_York",
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "CT": "America/Chicago",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "MT": "America/Denver",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "PT": "America/Los_Angeles",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "IST": "Asia/Kolkata",
    "UTC": "UTC",
    "GMT": "UTC",
}

_WEEKDAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def _parse_time(s: str) -> Optional[time]:
    s = s.strip().lower().replace(".", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])m?$", s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    period = m.group(3)
    if period == "p" and hour != 12:
        hour += 12
    if period == "a" and hour == 12:
        hour = 0
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return time(hour, minute)
    return None


def parse_hours(text: Optional[str]) -> Optional[HoursWindow]:
    """Best-effort parse of strings like 'Mon-Fri 8a-6p ET' or 'Mon-Fri 9-5 ET'."""
    if not text:
        return None
    s = text.strip()

    # timezone (default ET, since most US manufacturer rows say ET)
    tz_match = re.search(r"\b(ET|EDT|EST|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT|IST|UTC|GMT)\b", s)
    tz = ZoneInfo(_TZ_MAP[tz_match.group(1)]) if tz_match else ZoneInfo("America/New_York")

    # weekday range, e.g. "Mon-Fri" or "Mon, Tue, Wed" or "Mon-Sat"
    weekdays: set[int] = set()
    range_match = re.search(r"(mon|tue|wed|thu|fri|sat|sun)\s*[-–]\s*(mon|tue|wed|thu|fri|sat|sun)", s, re.I)
    if range_match:
        start = _WEEKDAY_MAP[range_match.group(1)[:3].lower()]
        end = _WEEKDAY_MAP[range_match.group(2)[:3].lower()]
        weekdays = {d % 7 for d in range(start, end + 1)} if end >= start else {start, end}
    else:
        for token in re.findall(r"(mon|tue|wed|thu|fri|sat|sun)", s, re.I):
            weekdays.add(_WEEKDAY_MAP[token[:3].lower()])
    if not weekdays:
        weekdays = {0, 1, 2, 3, 4}  # default Mon-Fri

    # time range like "8a-6p" / "9-5" / "8:30am-5:00pm"
    open_t = close_t = None
    time_pattern = r"(\d{1,2}(?::\d{2})?\s*[ap]?m?)\s*[-–]\s*(\d{1,2}(?::\d{2})?\s*[ap]?m?)"
    tm = re.search(time_pattern, s, re.I)
    if tm:
        raw_open, raw_close = tm.group(1), tm.group(2)
        # If only one side has am/pm, infer from order
        if not re.search(r"[ap]m?$", raw_open, re.I) and re.search(r"[ap]m?$", raw_close, re.I):
            # e.g. "8-5pm" -> "8am-5pm"
            suffix = "am" if "p" in raw_close.lower() else "pm"
            raw_open = f"{raw_open}{suffix}"
        if not re.search(r"[ap]m?$", raw_close, re.I) and re.search(r"[ap]m?$", raw_open, re.I):
            raw_close = f"{raw_close}pm"
        if not re.search(r"[ap]m?$", raw_open, re.I) and not re.search(r"[ap]m?$", raw_close, re.I):
            # bare digits — assume 9-5 means 9am-5pm
            raw_open = f"{raw_open}am"
            raw_close = f"{raw_close}pm"
        open_t = _parse_time(raw_open)
        close_t = _parse_time(raw_close)

    return HoursWindow(weekdays=weekdays, open_local=open_t, close_local=close_t, tz=tz)


def is_within_business_hours(text: Optional[str], now_utc: Optional[datetime] = None) -> Optional[bool]:
    """Returns True/False if we can determine, or None if we can't parse."""
    win = parse_hours(text)
    if not win or not win.open_local or not win.close_local:
        return None
    now_local = (now_utc or datetime.now(tz=ZoneInfo("UTC"))).astimezone(win.tz)
    if now_local.weekday() not in win.weekdays:
        return False
    now_t = now_local.time()
    return win.open_local <= now_t <= win.close_local


# ---------- Outbound call ----------

def place_inquiry_call(
    *,
    inquiry_id: int,
    to_number: str,
    manufacturer_name: str,
    subject: str,
    question: str,
    requester_name: Optional[str] = None,
    requester_email: Optional[str] = None,
) -> Dict[str, Any]:
    """Place an outbound call via ElevenLabs (which uses Twilio under the hood).

    Returns the JSON response from ElevenLabs (includes a `conversation_id` we
    store on the inquiry so we can match the post-call webhook back).
    """
    api_key = _required("ELEVENLABS_API_KEY")
    agent_id = _agent_id()
    phone_number_id = _phone_number_id()

    first_message = (
        f"Hello, this is the InpharmD medical information line calling on behalf of"
        f" a pharmacist with an inquiry regarding {manufacturer_name}. "
        f"I need to ask about: {subject}. Is this a good time?"
    )

    payload: Dict[str, Any] = {
        "agent_id": agent_id,
        "agent_phone_number_id": phone_number_id,
        "to_number": to_number,
        "conversation_initiation_client_data": {
            "conversation_config_override": {
                "agent": {
                    "first_message": first_message,
                },
            },
            "dynamic_variables": {
                "inquiry_id": str(inquiry_id),
                "manufacturer_name": manufacturer_name,
                "inquiry_subject": subject,
                "inquiry_question": question,
                "requester_name": requester_name or "",
                "requester_email": requester_email or "",
            },
        },
    }

    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{ELEVEN_BASE}/convai/twilio/outbound-call",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
        return r.json()
