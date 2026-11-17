"""Literal boundary for the Claude CLI's result document.

``claude -p --output-format json`` writes one JSON object per run: the session's
usage, the model's turns and, when the provider refused, the API error and the
message the user saw. Every decode, split and date reading lives here, so the
engine judges plain values and never parses the CLI's text itself.

The recorded refusal of 2026-11-17 (a weekly limit spent) is the shape this
module reads: ``is_error`` true, ``api_error`` ``usage_limit_reached``,
``api_error_status`` 429, ``api_error_origin`` ``first_party``, zero tokens and
``duration_api_ms`` zero, with the text "You've hit your weekly limit · resets
Nov 18, 3am (UTC)". A run that shows inference (tokens, turns or API time) had
already acted, so its effect is unknown rather than absent.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# The provider's own words for an account that cannot pay for another call: a spent weekly
# window, a spent plan, an empty balance. The first-party usage limit is named by the API
# field, the others by the message the user saw.
QUOTA_MARKERS = ("usage_limit_reached", "weekly limit", "usage limit", "insufficient_balance",
                 "credit balance is too low", "quota exceeded", "out of credits")

# A provider that answers a rate limit is not out of quota: the same request may succeed
# minutes later, so the router falls through at once and cools only that role.
RATE_LIMIT_MARKERS = ("rate_limit_error", "rate limit", "too many requests", "429 rate limit")

RESET = re.compile(r"resets\s+(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),\s*"
                    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)\s*\(UTC\)")


def load(path: Path) -> dict | None:
    """The run's result document, or None when it is missing, unreadable or not an object."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def inferred(document) -> bool:
    """True when the document shows the model actually answered.

    A tool call needs a model turn, and a model turn needs inference, so zero tokens with
    zero turns and zero API time means nothing ran: the refusal came before any effect.
    """
    if not isinstance(document, dict):
        return False
    usage = document.get("usage")
    if isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return True
    turns = document.get("num_turns")
    if isinstance(turns, int) and not isinstance(turns, bool) and turns > 1:
        return True
    duration = _number(document.get("duration_api_ms"))
    return duration is not None and duration > 0


def refusal(document) -> str | None:
    """The provider's refusal text for a failed run, else None.

    The API field names the error and the result text names what the user saw; the first
    part is what classifies, the second carries the reset time.
    """
    if not isinstance(document, dict) or document.get("is_error") is not True:
        return None
    parts = [part for part in (document.get("api_error"), document.get("result"))
             if isinstance(part, str) and part.strip()]
    return " ".join(parts)[:300] if parts else None


def is_quota_error(text: str | None) -> bool:
    """True when the refusal says the account is out of quota rather than merely slow."""
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in QUOTA_MARKERS)


def is_rate_limit_error(text: str | None) -> bool:
    """True when the refusal says the provider is rate limiting the request."""
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in RATE_LIMIT_MARKERS)


def reset_at(document, *, now: float) -> float | None:
    """The epoch seconds of the reset the provider named, else None.

    The message names a day and a time in UTC without a year. The year is the current one,
    and a reset already past means the next year, so a stale message cannot put the account
    back in service for a day that has gone.
    """
    if not isinstance(document, dict):
        return None
    text = document.get("result")
    if not isinstance(text, str):
        return None
    match = RESET.search(text)
    if match is None:
        return None
    parts = match.groupdict()
    month = MONTHS.get(parts["month"])
    if month is None:
        return None
    hour = int(parts["hour"])
    minute = int(parts["minute"] or 0)
    if hour > 12 or minute > 59:
        return None
    if parts["period"] == "pm" and hour != 12:
        hour += 12
    elif parts["period"] == "am" and hour == 12:
        hour = 0
    stamp = datetime.fromtimestamp(now, timezone.utc)
    try:
        when = datetime(stamp.year, month, int(parts["day"]), hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None
    if when.timestamp() <= now:
        when = when.replace(year=stamp.year + 1)
    return when.timestamp()
