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
import re

from .slugs import validate_slug


def checked_reviewer_model(model: object, source: str) -> str:
    """The reviewer model id, trimmed, or ValueError unless it is a Haiku id.

    Reading a configured model name is interpretation of external text, so the
    check lives at this boundary; the reviewer acts on the checked id.
    """
    text = str(model).strip()
    if not text.startswith("claude-haiku-"):
        raise ValueError(f"reviewer model from {source} must be a claude-haiku-* id, got {text!r}")
    return text


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


def heading_present(text: str, heading: str) -> bool:
    """Report whether a markdown heading line starts with ``heading``.

    Case-insensitive; ``heading`` may be a regular expression fragment such as
    ``blockers?``. Only heading lines count, so body text is never a heading.
    """
    return re.search(rf"^#+ *{heading}\b", text, re.IGNORECASE | re.MULTILINE) is not None


def ends_with_newline(text: str) -> bool:
    """Report whether a wall document already ends with a newline."""
    return text[-1:] == "\n"


def dm_recipient(text: str, source: str, roles=()) -> str | None:
    """The recipient of an addressed first line, or None.

    Canonical form, exactly as ``wall.addressed``: the first line is ``[dm] to=ROLE`` with a valid slug and the
    sender does not address itself. Minds also write natural addressed prose ("health to genome: ...", "To genome: ...");
    2026-11-05 Health's eight such messages to an idle Genome never woke it. That form addresses a recipient only when it
    names one of ``roles`` (declared roles), so ordinary prose beginning with "to" never touches an inbox.
    ``append`` uses it so a DM written through the plain CLI wakes its recipient like ``wall dm``.
    """
    first = text.splitlines()[0] if text else ""
    match = re.fullmatch(r"\[dm\] to=(\S+)", first)
    me = source.removeprefix("mind/")                          # the sender's own role, whether it signs as ROLE or mind/ROLE
    if match is None:
        natural = re.match(rf"(?:(?:{re.escape(source)}|{re.escape(me)})\s+)?to[=\s]\s*([a-z][a-z0-9-]*)(?![\w/-])", first, re.IGNORECASE)   # a malformed [dm] line stays rejected
        if natural is not None and natural.group(1).lower() in set(roles) and natural.group(1).lower() != me:
            return natural.group(1).lower()
        return None
    if match.group(1) in {me, f"mind/{me}"}:
        return None
    try:
        validate_slug(match.group(1))
    except ValueError:
        return None
    return match.group(1)


def near_miss_dm(text: str) -> str | None:
    """The role named by a first line that looks like a DM but is not exactly ``[dm] to=ROLE`` (words follow it), else None."""
    first = text.splitlines()[0] if text else ""
    near = re.match(r"\[dm\] to=(\S+)\s+\S", first)
    return near.group(1) if near else None


_RENDER_VOLATILE_AGE = re.compile(r" age=(?:[0-9]|<age>)[0-9.eE+\-]*(?=;)")
_RENDER_VOLATILE_CACHE = re.compile(r"cached_at=[0-9][0-9.eE+-]*")


def strip_render_volatile(body: str) -> str:
    """Blank the float age and cache stamp of a retained render deferral.

    Those values advance every render tick; the digest must not change with
    them. The state and reason stay in the text.
    """
    body = _RENDER_VOLATILE_AGE.sub(" age=…", body)
    return _RENDER_VOLATILE_CACHE.sub("cached_at=…", body)


def names_sender(body: str, sender: str) -> bool:
    """True when ``body`` names ``sender`` as a whole role token.

    A role inside a longer hyphenated name does not count: ``genome`` is not
    named by ``genome-2``.
    """
    return re.search(rf"(?<![\w-]){re.escape(sender)}(?![\w-])", body) is not None


def addressed(entry, role: str) -> bool:
    """Whether a feed entry is addressed to ROLE. Lives here so ``feed`` can import it at module level."""
    first = entry.body.splitlines()[0] if entry.body else ""
    if entry.source == "operator/permissions":
        decision = re.match(r"\[permission\] id=\S+ decision=(?:granted|revoked) owner=([A-Za-z0-9._:/-]+)(?:\s|$)", first)
        if decision:
            return decision.group(1) == role
    # One definition of "addressed" for waking AND reading: the canonical [dm] to=ROLE line, or natural prose that names this role
    # ("health to genome: ..."). A wake whose message the reader cannot find would be a new useless wake.
    return entry.source not in {role, f"mind/{role}"} and dm_recipient(entry.body, entry.source, (role,)) == role
