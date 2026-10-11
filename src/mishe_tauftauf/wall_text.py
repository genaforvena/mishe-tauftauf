"""Wall-note ownership comparison: the approved adapter for wall text form.

The restore document renders every peer wall undelimited into one text, so a
mind's notes can reproduce a peer's wall byte for byte and ``wall write`` /
``seed yield`` would accept the clobber. Deciding whether two wall documents
are the same document is an interpretation of external wall text, so it lives
here as a boundary, and the caller in ``wall.py`` acts on a plain answer.

The adapter compares documents, not formatting: trailing blank lines are not
authorship, so a paste that ends on a different blank-line count is still the
peer's document. A difference anywhere else is the writer's own text.
"""
from __future__ import annotations


def is_peer_copy(notes: str, peer_wall: bytes) -> bool:
    """Report whether ``notes`` are another role's wall document.

    ``notes`` is the text a mind asked to write; ``peer_wall`` is the raw
    bytes of a peer's existing wall file. Returns ``True`` when the two are the
    same document ignoring trailing blank lines.
    """
    return notes.encode("utf-8").rstrip(b"\n") == peer_wall.rstrip(b"\n")
