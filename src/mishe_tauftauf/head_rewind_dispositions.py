"""Disposition records for head-rewind paths (sense.repo.head-rewinds).

A record is one mind's author claim that a path the head-rewinds reading left
as needs-review was dealt with: it lives at
``SITE/dispositions/head-rewinds/<commit[:12]>-<sha256(path)[:16]>.json`` and
names the dropped commit, the path, and the blob that commit carried for it.

The reader matches a record only when all three of commit, path and blob equal
what git reports for that dropped commit, so a record cannot clear a path whose
bytes have changed since it was written. A record that fails validation is
ignored and reported as malformed; its path stays needs-review. The verdict is
recorded beside the reading and never clears drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

RECORD_DIR = "head-rewinds"
VERDICTS = frozenset({"superseded-by-revision"})
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_TEXT_FIELDS = ("commit", "path", "blob", "verdict", "reason", "author", "head_checked")


def record_name(commit: str, path: str) -> str:
    return f"{commit[:12]}-{hashlib.sha256(path.encode()).hexdigest()[:16]}.json"


def _valid(record: object, name: str) -> bool:
    if not isinstance(record, dict):
        return False
    if any(not isinstance(record.get(field), str) or not record[field].strip()
           for field in _TEXT_FIELDS):
        return False
    if not isinstance(record.get("wake"), int) or isinstance(record.get("wake"), bool):
        return False
    commit, path, blob = record["commit"], record["path"], record["blob"]
    if not _HEX40.match(commit) or not _HEX40.match(blob):
        return False
    if path.startswith("/") or ".." in Path(path).parts:
        return False
    if record["verdict"] not in VERDICTS:
        return False
    return name == record_name(commit, path)


def load(home: Path) -> tuple[dict[tuple[str, str, str], dict], int] | None:
    """Return ``({(commit, path, blob): record}, malformed_count)``.

    None when the record directory exists but cannot be listed: the reader
    then reports the records as unreadable and leaves every path needs-review.
    An absent directory is zero records, not an error.
    """
    root = Path(home) / "dispositions" / RECORD_DIR
    if not root.exists():
        return {}, 0
    try:
        entries = sorted(root.glob("*.json"))
    except OSError:
        return None
    index: dict[tuple[str, str, str], dict] = {}
    malformed = 0
    for entry in entries:
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            malformed += 1
            continue
        if not _valid(record, entry.name):
            malformed += 1
            continue
        index[(record["commit"], record["path"], record["blob"])] = record
    return index, malformed
