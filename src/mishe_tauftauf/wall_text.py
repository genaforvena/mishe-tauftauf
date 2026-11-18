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


def first_line(text: str) -> str:
    """The first line of a message body; later lines are prose."""
    return text.splitlines()[0] if text else ""


_TAG_RE = re.compile(r"^\[([a-z0-9-]+)\]")


def record_tag(text: str) -> str | None:
    """The bracketed record tag of a message's first line (``[ci] ...`` -> ``ci``), or None.

    This is the one form a classifier may read from a first line besides the DM
    address forms; the rest of the line and body stay opaque prose.
    """
    first = text.splitlines()[0] if text else ""
    match = _TAG_RE.match(first)
    return match.group(1) if match else None


def message_subject(text: str) -> str:
    """The first markdown heading after the first line, stripped of '#' and spaces; '' when none.

    A bounded subject line is the one prose form a classifier may read from a body;
    everything past it stays opaque.
    """
    lines = text.splitlines()
    for line in lines[1:]:
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""

_DM_ADDRESS_RE = re.compile(r"\[dm\] to=\S+")
_REQUEST_RE = re.compile(
    r"^\s*(?:please\b|request\s*:|could you\b|can you\b|i need (?:you|your)\b)"
    r"|\breview request\b|\bneeds your call\b", re.IGNORECASE)


def message_excerpt(text: str) -> str:
    """The first explicit request line, else the first prose line; '' when none.

    Live addressed DMs carry their content as plain prose after the ``[dm] to=ROLE``
    line, not as a markdown heading, so ``message_subject`` sees nothing there.
    This is the one bounded prose line the classifier may read instead; the
    caller caps it at the same excerpt budget as ``first_line`` and ``subject``.
    Some walls repeat the address line, so a second ``[dm] to=ROLE`` is not content.
    A long DM often opens with a short lead-in ("Three results, one request.") and
    states its ask in a later paragraph, so prefer a line with an explicit request
    ("Please review", "Request:", "review request"); fall back to the first prose
    line when there is none. Both selections skip address lines.
    """
    prose = [line.strip() for line in text.splitlines()[1:]]
    for line in prose:
        if line and not _DM_ADDRESS_RE.fullmatch(line) and _REQUEST_RE.search(line):
            return line
    for line in prose:
        if line and not _DM_ADDRESS_RE.fullmatch(line):
            return line
    return ""

def sender_role(source: str) -> str:
    """The sender's own role from a feed source, whether it signs as ROLE or mind/ROLE."""
    return source.removeprefix("mind/")


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


FAILURE_NOTICE = ("{unit} failed. Signal {name} is RED. Find the cause "
                  "(journalctl --user -u {unit}), fix or disposition it, "
                  "then `mishe signal observe {name} GREEN`.")
_DM_ADDRESS_LINE = re.compile(r"\[dm\] to=\S+[ \t]*\n")
_FAILURE_NOTICE_RE = re.compile(
    r"^(?P<unit>\S+) failed\. Signal (?P<signal>\S+) is RED\. Find the cause "
    r"\(journalctl --user -u (?P=unit)\), fix or disposition it, then "
    r"`mishe signal observe (?P=signal) GREEN`\.$")


def failure_notice(text: str) -> tuple[str, str] | None:
    """The (unit, signal) a machine failure notice names, or None.

    ``failure.report`` writes exactly one sentence after an OnFailure, and
    ``wall.message`` addresses it with ``[dm] to=ROLE`` on the line before, so the
    class of such a message is a lookup, not a judgment: the DM classifier can
    label it without a decision-model call.
    """
    body = text.strip()
    addressed = _DM_ADDRESS_LINE.match(body)
    if addressed:
        body = body[addressed.end():]
    match = _FAILURE_NOTICE_RE.match(body)
    return (match.group("unit"), match.group("signal")) if match else None


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
