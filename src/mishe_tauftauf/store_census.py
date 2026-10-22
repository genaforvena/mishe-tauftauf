"""The outcome store's population, and the part no tape entry names.

A reading that counts rows has to say which rows it counted, and what it
could not see. The audited population of the evidence-binding reading is the
post-clause outcome publications the current tape names. The store holds more
outcome records than that, and the difference is the population no reading of
the tape can audit: a store record carries no publication instant, so the
split between the records that fall outside the clause window and the records
whose tape entry is gone cannot be told from the store either.

The census reads the store's record names and opens only the records no tape
entry names, so its cost follows the population it measures rather than the
whole archive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import records
from .outcome_events import parse_feed

#: A record reference in a tape entry names the store record's content digest.
#: The marker is searched for directly rather than through a regular
#: expression: the tape is tens of megabytes of entry bodies, and the literal
#: search is the cheap way to ask every body which records it names.
RECORD_MARKER = "[record] records/"

#: The hex alphabet a record digest is written in.
_HEX = frozenset("0123456789abcdef")

#: How many store records the census opens at most. A census that cannot
#: finish is published as partial, never as a complete count.
CENSUS_FILE_LIMIT = 100000


def _add_named(body: str, named: set[str]) -> None:
    """The record digests one tape entry's body names."""
    start = 0
    while True:
        index = body.find(RECORD_MARKER, start)
        if index < 0:
            return
        start = index + len(RECORD_MARKER)
        digest = body[start:start + 64]
        if len(digest) == 64 and not _HEX.issuperset(digest):
            continue
        named.add(digest)


def tape_entries(tape: Path, home: Path, named: set[str]):
    """The tape's entries, and the store record digests they name.

    The tape is read through the classifier's own seam, so a supplied
    snapshot and the live tape are read the same way and the census sees
    exactly the records the classifier's entries name. The digests are
    collected as the entries stream past, so the tape is never held whole.
    """
    for entry in parse_feed(tape, home=home):
        _add_named(entry.body, named)
        yield entry


def store_census(home: Path, named: set[str]) -> dict:
    """The store records no tape entry names, and how many of them are outcomes.

    ``named`` holds the record digests the tape names. The census measures
    the complement: the records the tape cannot show a reader, which is the
    population the evidence-binding reading does not audit.
    """
    try:
        digests = set(records.record_digests(home))
    except OSError as exc:
        return {"state": "unknown", "reason": "outcome store unreadable: " + str(exc)}
    complement = sorted(digests - named)
    kinds: dict[str, int] = {}
    unreadable = 0
    unclassified = 0
    for digest in complement[:CENSUS_FILE_LIMIT]:
        head = records.record_head(home, digest)
        if head is None:
            unreadable += 1
            continue
        kind = records.record_kind(head)
        if kind is None:
            unclassified += 1
            continue
        kinds[kind] = kinds.get(kind, 0) + 1
    complete = (len(complement) <= CENSUS_FILE_LIMIT and unreadable == 0
                and unclassified == 0)
    return {
        "state": "verified" if complete else "partial",
        "store_records": len(digests),
        "tape_named_records": len(digests & named),
        "records_without_tape_entry": len(complement),
        "wall_outcome": kinds.get("wall-outcome", 0),
        "kinds": dict(sorted(kinds.items())),
        "unreadable": unreadable,
        "unclassified": unclassified,
        "census_complete": complete,
        "measured_at": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
