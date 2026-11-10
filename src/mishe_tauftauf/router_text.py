"""Approved parsing adapter for the engine router, the engine's own stream and
the wake-concurrency JSON files."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path


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


def ledger_events(path: Path):
    """The decoded routing-ledger events, oldest first.

    A line that is not a JSON object is not an event: it is skipped, so a
    truncated ledger still yields the events it does hold. A missing ledger
    yields no events.
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


def iso_ms(value) -> float | None:
    """An ISO-8601 UTC timestamp as epoch milliseconds, or None when it cannot be read."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000
    except (TypeError, ValueError):
        return None
