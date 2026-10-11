"""Outcome publication dialect: the approved adapter for wall-outcome tape text.

A wall outcome is published on the chat tape as a reserved header, the mind's
prose, and one immutable record reference:

    Wall outcome <kind> by <role>
    <prose>
    [record] records/<digest>.json sha256=<digest>

The writer (``wall.py``) renders that shape and ``records.py`` verifies the
reference; this adapter owns everything *between* the bytes and a decision, so
the two consumers that read the same publication — the evidence-coverage
classifier in ``outcome_events.py`` and the analysis advisor's previous-context
reader in ``witness_analysis.py`` — never re-derive the syntax themselves.

Recognition and validation are deliberately separate here. A classifier must
treat a reserved-but-damaged publication as a coverage gap rather than drop it,
so it recognizes a candidate by prefix and then validates it against decoded
pieces; a verifier needs the whole publication or nothing, so
:func:`decode_publication_body` returns one decoded publication or ``None``.
Both get their syntax from the same place, which is what keeps a malformed
publication from meaning one thing to each.

The vocabulary is deliberately narrow. A header is one full line: the reserved
prefix, a supported kind, and a role of lowercase letters, digits and hyphens.
A prose line is anything else, so a mind's evidence can quote a reserved header
without impersonating a publication — the classifier's own rule that the header
must be the first significant line of the publication decides whether the quote
is a candidate at all. A reference is one line naming a content-addressed
record. Everything the dialect cannot decode stays undecoded: the adapter
returns ``None`` rather than guessing, so an unreadable publication reaches the
consumer as a gap it must report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from .records import REFERENCE_RE

OUTCOME_KINDS = ('accepted', 'blocker-resolved', 'blocker-retired', 'hypothesis-changed')
"""The publication kinds the reserved header may name."""

_HEADER = re.compile(r'Wall outcome (' + '|'.join(OUTCOME_KINDS) + r') by ([a-z0-9-]+)\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
OUTCOME_PREFIX = 'Wall outcome '
WORK_CHANNEL_PREFIX = '[work] channel=witness '
HANDOFF_MARKER = 'HANDOFF:\n'


@dataclass(frozen=True)
class OutcomePublication:
    """One decoded wall-outcome publication body.

    ``text`` is the prose between the header and the final reference line, as
    the body carries it; the record's own payload text is a separate fact the
    consumer compares against it. ``reference`` is the standalone final
    ``[record]`` line and ``digest`` the record digest it names.
    """

    kind: str
    role: str
    text: str
    reference: str
    digest: str


def body_lines(body: str) -> list[str]:
    """The body's lines, in order, without their terminators."""
    return body.splitlines()


def has_outcome_prefix(line: str) -> bool:
    """Report whether a line opens the reserved outcome header.

    Leading blanks are ignored because a publication may be indented inside a
    larger document; the prefix alone identifies a candidate, not a valid one.
    """
    return line.lstrip().startswith(OUTCOME_PREFIX)


def starts_outcome_publication(body: str) -> bool:
    """Report whether a body opens with the reserved header, exactly.

    Not indented and not blank-first: this is the recognition a bare
    publication gets on the tape, where no larger document wraps it.
    """
    return body.startswith(OUTCOME_PREFIX)


def first_significant_line(lines: list[str]) -> str:
    """The first line carrying content, or empty text when none does."""
    return next((line for line in lines if line.strip()), '')


def parse_header(line: str) -> tuple[str, str] | None:
    """Decode one full header line into its kind and role, or None.

    Exactly one line, so a header with trailing prose or a prefix reused as a
    sentence opener is not a header.
    """
    match = _HEADER.fullmatch(line)
    return None if match is None else (match.group(1), match.group(2))


def reference_lines(lines: list[str]) -> list[str]:
    """The publication's record-reference lines, in order.

    A reference is recognized after leading blanks, matching the reference
    check ``records.py`` performs, so an indented reference is not invisible
    to one consumer and visible to the other.
    """
    return [line for line in lines if line.lstrip().startswith('[record]')]


def sole_final_reference(lines: list[str]) -> str | None:
    """The one reference line that is also the last line, or None.

    A publication carries its reference last and alone: a second reference
    line or trailing prose after it means the body is not the shape the
    dialect prescribes, so it is not decoded rather than decoded loosely.
    """
    refs = reference_lines(lines)
    if len(refs) != 1 or not lines or refs[0] != lines[-1]:
        return None
    return refs[0]


def reference_digest(line: str) -> str | None:
    """The record digest a reference line names, or None when it names none."""
    match = REFERENCE_RE.fullmatch(line)
    return None if match is None else match.group(1)


def decode_publication_body(body: str) -> OutcomePublication | None:
    """Decode a complete wall-outcome publication body, or None.

    Requires the strict header on the first line, prose, and exactly one
    record reference that is the final line. Returns ``None`` for anything
    else — a damaged, displaced or replaced publication — so the caller
    reports a gap instead of reading a different publication than the tape
    carries.
    """
    lines = body_lines(body)
    if not lines:
        return None
    header = parse_header(lines[0])
    if header is None:
        return None
    reference = sole_final_reference(lines)
    if reference is None:
        return None
    digest = reference_digest(reference)
    if digest is None:
        return None
    kind, role = header
    return OutcomePublication(kind=kind, role=role,
                              text='\n'.join(lines[1:-1]),
                              reference=reference, digest=digest)


def publication_prose(lines: list[str]) -> str:
    """The prose a body carries between its header and its final reference."""
    return '\n'.join(lines[1:-1])


def decode_timestamp(text: str) -> datetime:
    """Decode a feed entry's UTC stamp into an aware datetime.

    Raises :class:`ValueError` for a malformed stamp and :class:`TypeError`
    for a non-string, so the caller records the publication as incomplete
    instead of comparing it against an unreadable time.
    """
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def decode_text(value: object) -> str | None:
    """The reported text when it is a string carrying content, else None.

    Whitespace-only prose is not a contribution, so it reads as absent rather
    than as a valid empty one.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def decode_evidence(value: object) -> dict | None:
    """The bound evidence when it carries a path and a digest, else None.

    The binding names one write-once file inside the owned site and the digest
    of the bytes recorded with it; a missing path, a blank one or a digest that
    is not a hex SHA-256 is not a binding, so the consumer reports the gap
    instead of reading evidence that was never bound.
    """
    if not isinstance(value, dict):
        return None
    path, digest = value.get('path'), value.get('sha256')
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        return None
    return {'path': path, 'sha256': digest}


def decode_work_handoff(body: str) -> str | None:
    """The witness work-channel handoff text a seed body carries, or None.

    The work channel is the legacy handoff dialect: a ``[work]`` header naming
    the witness channel followed by the handoff text after its marker. A body
    without the channel header is not a handoff, so it is distinguished from a
    handoff whose marker is missing rather than treated as one.
    """
    if not body.startswith(WORK_CHANNEL_PREFIX):
        return None
    return body.split(HANDOFF_MARKER, 1)[-1]


def decode_excerpt(body: str) -> str:
    """The body's prose with framed JSON lines removed.

    A task-state publication frames its explanation as JSON, and the complete
    source stays available by sequence and body hash; the excerpt keeps the
    producer's readable lines only, so an advisor reads prose rather than a
    transcript of a control frame.
    """
    return '\n'.join(line for line in body_lines(body)
                     if not line.startswith('{'))
