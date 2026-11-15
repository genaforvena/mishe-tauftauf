"""Literal boundary for the codex CLI's external text: rollout files and the ``--json`` stream.

The codex CLI writes two kinds of text this engine reads: its session rollouts under
``~/.codex/sessions`` (``rate_limits`` readings, the only account evidence used) and the
JSONL event stream of one ``codex exec`` run (the effects, the errors). Both are external
protocol text, so every decode, split, timestamp and JSON read lives here. The callers
(``codex_usage`` and ``engine_codex``) judge windows, effects and errors; they never parse.

A malformed line yields None and is skipped by the caller. Nothing here trusts the text.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def parse_timestamp(stamp) -> float | None:
    """Epoch seconds of an ISO-8601 stamp such as ``2026-11-08T10:00:00Z``, or None."""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def tail_lines(data: bytes, *, cut: bool) -> list[str]:
    """Lines of a byte tail; when ``cut`` the first line may be a fragment and is dropped."""
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if cut else lines


def json_line(line: str) -> dict | None:
    """The JSON object on one stream line, or None for a non-object, malformed or blank line."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def event_type(event: dict) -> str:
    value = event.get("type")
    return value if isinstance(value, str) else ""


def is_item_event(event: dict) -> bool:
    """True for an ``item.*`` event; the caller checks the item kind."""
    return event_type(event).startswith("item.")
