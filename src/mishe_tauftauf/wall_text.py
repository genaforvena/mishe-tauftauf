"""Wall-note ownership comparison: the approved adapter for wall text form.

The restore document renders every peer wall undelimited into one text, so a
mind's notes can reproduce a peer's wall byte for byte and ``wall write`` /
``seed yield`` would accept the clobber. Deciding whether two wall documents
are the same document is an interpretation of external wall text, so it lives
here as a boundary, and the caller in ``wall.py`` acts on a plain answer.

The adapter compares documents, not formatting: trailing blank lines are not
authorship, so a paste that ends on a different blank-line count is still the
peer's document. A difference anywhere else is the writer's own text.

The same document identity, as a digest, lets a caller compare a note against
documents that no longer exist as files — the write audit's history — so the
digest and the audit-log decoding live here too, beside the comparison they
share their normalization with.
"""
from __future__ import annotations

import hashlib
import json


def is_peer_copy(notes: str, peer_wall: bytes) -> bool:
    """Report whether ``notes`` are another role's wall document.

    ``notes`` is the text a mind asked to write; ``peer_wall`` is the raw
    bytes of a peer's existing wall file. Returns ``True`` when the two are the
    same document ignoring trailing blank lines.
    """
    return notes.encode("utf-8").rstrip(b"\n") == peer_wall.rstrip(b"\n")


def document_digest(notes: str) -> str:
    """Stable digest of one wall document under the same normalization.

    Trailing blank lines are ignored, so a rewrite that only changes the final
    blank-line count keeps the document's identity.
    """
    return hashlib.sha256(notes.encode("utf-8").rstrip(b"\n")).hexdigest()


def decode_audit_entries(text: str) -> list[dict]:
    """Decode the wall-write audit log into entries, oldest first.

    One JSON object per line; blank and undecodable lines are skipped so a
    truncated tail never fails a write's ownership check. Entries are returned
    in file order, which is write order.
    """
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries
