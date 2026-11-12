"""Approved parsing adapter for the engine router, the engine's own stream and
the wake-concurrency JSON files."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re


RETRY_AFTER_MS = re.compile(r"retry-after-ms=(\d+)")


def retry_after_ms(text) -> int | None:
    """The provider's stated retry-after-ms in an error string, else None.

    Only digits after the literal marker count; anything else returns None and
    the caller falls back to its own cool-off policy.
    """
    m = RETRY_AFTER_MS.search(str(text))
    return int(m.group(1)) if m else None


def load_json(path: Path):
    """Decode a UTF-8 JSON file; raises OSError/ValueError like json.loads."""
    return json.loads(path.read_text(encoding="utf-8"))


def stream_events(path: Path):
    """Yield the decoded events of an engine JSON-lines stream, oldest first.

    A line that is not a JSON object is not an event: it is skipped, so a
    truncated or interleaved stream yields the events it does hold. A missing
    stream yields no events. The engine envelope is read here, once.

    The events are yielded as they are decoded: an engine stream runs to tens
    of megabytes, and a caller that stops at the first tool call must not pay
    for the rest of the file.
    """
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


def split_cooling_key(key: str) -> tuple:
    """``role|candidate`` -> (role, candidate); a bare candidate has role None."""
    if "|" not in key:
        return None, key
    role, _, cand = key.partition("|")
    return role, cand


def file_lines(path: Path) -> list:
    return path.read_text(encoding="utf-8").splitlines()


def ledger_events(path: Path, skipped: list | None = None):
    """The decoded routing-ledger events, oldest first.

    A line that is not a JSON object is not an event: it is skipped, so a
    truncated ledger still yields the events it does hold. A missing ledger
    yields no events. Pass a list as `skipped` to receive the 1-based line
    numbers of the rows that were skipped, so a caller can report them.
    """
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as lines:
        for number, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            if isinstance(event, dict):
                yield event
            elif skipped is not None:
                skipped.append(number)


def iso_ms(value) -> float | None:
    """An ISO-8601 UTC timestamp as epoch milliseconds, or None when it cannot be read."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000
    except (TypeError, ValueError):
        return None


def decode_events(text: str):
    """Decode a complete in-memory engine JSON-lines stream, oldest first.

    Unlike ``stream_events`` this is strict: a blank line is ignored, but any
    other line that is not a JSON object makes the whole stream undecodable
    (None). Use it where a truncated or interleaved stream must not be read as
    the events it happens to contain.
    """
    if not isinstance(text, str):
        return None
    events = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        events.append(event)
    return events
