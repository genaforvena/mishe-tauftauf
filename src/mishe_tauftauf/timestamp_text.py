"""Decode ISO-8601 instants from text.

The plant's logs, receipts and records carry their instants as ISO-8601 text
with an offset. That is an external dialect, so it is decoded here and the
consumer reads an instant rather than a string.
"""

from datetime import datetime, timezone


def decode_listed_next_instants(listing: object) -> list[datetime]:
    """The instants in the NEXT column of ``systemctl list-timers --plain`` rows.

    Each row starts with its NEXT time (``Sat 2026-11-14 05:10:58 UTC``). A row
    whose first columns are not that timestamp is skipped, so a caller never
    takes an unparseable line for a due time.
    """
    if not isinstance(listing, str):
        return []
    instants: list[datetime] = []
    for line in listing.splitlines():
        tokens = line.split()
        if len(tokens) < 5:
            continue
        try:
            nxt = datetime.strptime(" ".join(tokens[:4]), "%a %Y-%m-%d %H:%M:%S %Z")
        except ValueError:
            continue
        instants.append(nxt.replace(tzinfo=timezone.utc))
    return instants


def decode_iso_datetime(text: object) -> datetime | None:
    """The instant an ISO-8601 timestamp names, or ``None`` when it names none.

    A timestamp without an offset is read as UTC, the plant's clock domain:
    an instant without a zone is not a different instant, and treating it as
    local time would move it by the reader's offset. Text that is not a
    timestamp yields ``None`` rather than a guess, so a caller never
    computes an age against a moment that was never stated.
    """
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment
