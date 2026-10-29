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

Provenance vocabulary (C2PA Content Credentials 2.2, 2025-05-01) — additive:
typed, pure, verdict-free facts beside the wall-outcome decoder so a published
artifact can name its ingredients, disclosure level and validation state without
changing any ``Feed.append``, ``seed yield`` or DEV publish behaviour.
``Ch. 1 Guiding Principles box p.2 SHOULD NOT provide value judgments``;
``Ch. 2 Glossary pp.6-8 / Ch. 6 Assertions pp.23-26 / Ch. 10 Claims pp.36-45
/ Ch. 11 Manifests pp.47-56 / Sec. 18.15 Ingredient pp.186-197`` define
``Manifest / Claim / Assertion / ingredient``; ``Ch. 9 pp.34-35 / Sec. 18.5
Data Hash pp.130-132`` define hard bindings (``c2pa.hash.data`` sha256) and
``Sec. 18.10 Soft Binding pp.162-166`` the perceptual alternative;
``Ch. 10.3-10.4 pp.39-45 / Fig. 13 p.73`` define claim creation with
``c2pa.actions`` (``created|edited|published``); ``Ch. 14 Trust Model
pp.63-72 / Ch. 15 Validation pp.73-105 esp. Sec. 15.2 pp.74-78`` define
``Well-Formed / Valid / Trusted`` and status codes
``claimSignature.validated / assertion.dataHash.match / signingCredential.trusted``;
``Sec. 14.4-14.5 p.72`` define the trust list and OCSP ``rVals`` that make
``Trusted`` checkable; ``Ch. 16.3 Disclosure Levels p.111 / Sec. 6.8 p.25
redactedAssertions`` define ``minimal / standard / full``; ``Ch. 17
pp.113-114 / Sec. 14.2 p.63`` list harms/misuse and the open-problem boundary.
For UTF-8 tape text the hard binding is ``sha256(bytes)`` — already the
``[record]`` hex64 but now labeled ``c2pa.hash.data``; no soft binding for
prose; embedding is external manifest per ``Ch. 11.4-11.5 pp.55-56`` not JUMBF
in markdown. No X.509/COSE signing, JUMBF, watermark, trust-list fetch or
assertion redaction is implemented here; the vocabulary only makes
“published = authentic” checkable from bytes.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from .records import REFERENCE_RE

OUTCOME_KINDS: Final[tuple[str, ...]] = ('accepted', 'blocker-resolved', 'blocker-retired', 'hypothesis-changed')
"""The publication kinds the reserved header may name."""

_HEADER = re.compile(r'Wall outcome (' + '|'.join(OUTCOME_KINDS) + r') by ([a-z0-9-]+)\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
OUTCOME_PREFIX: Final[str] = 'Wall outcome '
WORK_CHANNEL_PREFIX: Final[str] = '[work] channel=witness '
HANDOFF_MARKER: Final[str] = 'HANDOFF:\n'

# --- Provenance vocabulary (C2PA 2.2) — Ch.1 box p.2 SHOULD NOT; Ch.2/6/10/11/18.15; Ch.9/18.5 hard; 18.10 soft; 15.2 status; 14.3-14.5 states; 16.3 disclosure; 17 harms ---
DISCLOSURE_LEVELS: Final[tuple[str, ...]] = ("minimal", "standard", "full")
"""Disclosure levels per C2PA Ch.16.3 p.111 (minimal/standard/full) — creator control, cf. Ch.1 p.2 privacy control and Sec.6.8 redactedAssertions analogue. No assertion is redacted by this vocab."""

MANIFEST_STATES: Final[tuple[str, ...]] = ("well-formed", "valid", "trusted")
"""Manifest validation states per C2PA Sec.14.3/15.3 pp.64,84-85 (Well-Formed -> Valid (hash+signature) -> Trusted (signer in trust list)). C2PA SHOULD NOT provide value judgments (Ch.1 box p.2) — trusted binding is not truth."""

PROVENANCE_ALG: Final[str] = "sha256"
"""Hash alg for hard binding per C2PA Sec.13.1 p.58 / Sec.18.5 p.130 (c2pa.hash.data alg set sha256|sha384|sha512); this plant uses sha256 for tape/markdown hard bindings."""

TRUSTED_SIGNERS: Final[tuple[str, ...]] = ()
"""Placeholder trust list per C2PA Sec.14.4 p.63 — future Trusted anchor; empty means no signer is Trusted yet, so Valid is the ceiling. No network fetch here."""

# --- Golaszewski critique vocabulary (arXiv:2604.24890v1 + IACR 2026/804 + AFIP 2026-04-20) — 5 goals vs 2 claimed, revocation, timestamp binding, strong vs weak, validator consistency, stripping ---
PROVENANCE_SECURITY_GOALS: Final[tuple[str, ...]] = ("claim-integrity", "weak-file-integrity", "timestamp-agreement", "validator-consistency", "strong-file-integrity")
"""Five security goals per Golaszewski 2026 p.2 — two C2PA-claimed (claim-integrity, weak-file-integrity) + three essential (timestamp-agreement, validator-consistency, strong-file-integrity). weak vs strong kept distinct."""

PROVENANCE_REVOCATION_STATES: Final[tuple[str, ...]] = ("revoked", "not-revoked", "unchecked")
"""Revocation trichotomy per Golaszewski p.3-4 Fig.2 — revoked / not-revoked / unchecked (unchecked is the spec-legal default that defeats all goals)."""

PROVENANCE_FILE_INTEGRITY_LEVELS: Final[tuple[str, ...]] = ("weak", "strong")
"""File-integrity levels per Golaszewski p.4 Fig.3 — weak (excluded bytes ignored) vs strong (every byte protected)."""
EVIDENCE_REMEDY_SUFFIX = re.compile(r'^(?P<stem>.+?)-(?P<sfx>v\d+|final|final-v\d+)$')
"""The clause's own remedy-naming convention, read as an artifact's name.

The vocabulary names the corrected **artifact** (``-v2``, ``-final``), not a
**status about** it. A ``-ci`` suffix names the CI disposition of the work —
the run URL, the head SHA, the outcome — and such a note carries none of the
lost artifact's content, so it cannot be evidence that the work was
re-evidenced. Measured over the plant's live ledger, that is the difference
between the two: of the ten remedy-name links, the nine naming a corrected
artifact were repairs and the single ``-ci`` one was a citation that
satisfied the naming convention without carrying the work. A convention that
a status note can satisfy is not a convention about the artifact, so the
suffix is not in the vocabulary.
"""


def remedy_stem(value, vocabulary=None) -> str | None:
    """The lost artifact's stem when the named path is its corrected form.

    ``None`` when the path is not named under the remedy vocabulary, so the
    caller sees no relation rather than a wrong one. The vocabulary is an
    argument so the same population can be read under a different vocabulary
    without editing this adapter.
    """
    pattern = vocabulary if vocabulary is not None else EVIDENCE_REMEDY_SUFFIX
    match = pattern.match(Path(value).stem)
    return match.group("stem") if match else None


def remedy_successor(lost_path, successor_path) -> bool:
    """Whether the successor names the lost artifact's corrected form.

    The clause's remedy naming, applied in the lost artifact's own path
    namespace: the successor's stem is the lost artifact's stem plus a remedy
    suffix, and both live in the same directory. The directory rule matters
    because two different losses can share a stem, and one successor cannot be
    the corrected form of both.

    The relation is decidable without reading prose, which is why a reader can
    apply it without interpreting an artifact's content.
    """
    lost = Path(lost_path)
    successor = Path(successor_path)
    stem = remedy_stem(successor)
    return stem is not None and stem == lost.stem and successor.parent == lost.parent


def _contained_parts(home, value):
    """The site-relative parts of a path inside the owned site, else None.

    Containment is the rule that predates the allowlist: the bound file must
    be inside the owned site, with no traversal and no symlink component.
    """
    from pathlib import Path
    home = Path(home).resolve()
    path = Path(value)
    if path.is_absolute():
        try:
            relative = path.relative_to(home)
        except ValueError:
            return None
    else:
        relative = path
    if any(part in {".", ".."} for part in relative.parts):
        return None
    candidate = path if path.is_absolute() else home / path
    try:
        resolved = candidate.resolve()
        parts = resolved.relative_to(home).parts
    except (OSError, RuntimeError, ValueError):
        return None
    current = home
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None
    return parts


def contained_evidence_path(home, value) -> bool:
    """Whether the path names a file inside the owned site.

    This is the rule that was in force before the 2026-10-16 allowlist
    amendment: a single file inside the owned site, with no traversal and no
    symlink component. A row bound before the amendment that names such a
    path was compliant then, so the current allowlist's exclusion of it is a
    policy version rather than a violation under the rule then in force. A
    path outside the site was never eligible under either rule, so it is
    never a version.
    """
    return _contained_parts(home, value) is not None


def eligible_evidence_path(home, value) -> bool:
    """Decode an owned-site evidence path against the explicit allowlist.

    The caller checks existence and nonempty bytes. This predicate also serves
    the observer, where a missing formerly eligible path must remain class C.
    """
    parts = _contained_parts(home, value)
    if parts is None:
        return False
    if len(parts) == 2 and parts[0] == "artifacts":
        return True
    if len(parts) == 2 and parts[0] == "discovery":
        return parts[1].startswith("scan-") and parts[1].endswith(".json")
    if len(parts) == 2 and parts[0] == "dms":
        return parts[1].endswith(".md")
    return False


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


def decode_supersedes(value: object) -> list[str]:
    """The record digests a publication claims to supersede.

    The supersession link is the plant's decidable work-identity relation: an
    outcome that re-evidences an earlier one names that outcome's record
    digest in a structured field, so a consumer can check the link without
    reading prose. The digests are decoded values, so the link is admissible
    under the parsing policy where a digest mentioned in prose is not.

    A value that is not a list of record digests decodes to no link rather
    than to a guess: a malformed field must not be read as a claim, and a
    publication that carries none is simply a publication that supersedes
    nothing.

    A list holding **any** malformed element decodes to no link at all, not
    to the well-formed part of it. A field that claims two supersessions and
    names one of them badly is a malformed field; keeping the element that
    happens to parse would let a malformed publication dispose a real row's
    debt, which is the failure the field's own contract forbids.
    """
    if not isinstance(value, list):
        return []
    digests = []
    for item in value:
        if not isinstance(item, str) or _DIGEST.fullmatch(item) is None:
            return []
        digests.append(item)
    return digests


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

def provenance_data_hash(data: bytes) -> str:
    """Hard binding for tape/markdown bytes — c2pa.hash.data (C2PA Sec.18.5 p.130, alg sha256).

    Pure ``hashlib.sha256(data).hexdigest()`` with no I/O; the bound is the
    64-hex sha256 already carried as ``[record] ... sha256=<hex>`` on the final
    reference line, now labeled as ``c2pa.hash.data`` so a reader can check
    ``assertion.dataHash.match`` without re-deriving the hash. Soft binding
    (C2PA Sec.18.10 watermark) does not apply to prose — there is no perceptual
    hash for UTF-8 tape text (App.A lists JPEG/PNG/MP4/PDF containers only).
    """
    return hashlib.sha256(data).hexdigest()


def provenance_ingredient(path: str, sha256: str, *, state: str = "valid") -> dict | None:
    """Typed ingredient record mirroring C2PA Sec.18.15 c2pa.ingredient.

    Returns ``{"path": ..., "sha256": ..., "alg": "sha256", "validationState": state}``
    when ``sha256`` is a 64-hex ``_DIGEST`` and ``state`` is in ``MANIFEST_STATES``
    (``well-formed``/``valid``/``trusted`` per Sec.14.3/15.3), else ``None``.
    ``None``-on-mismatch is the vocabulary's denier — no invented verdict.
    ``path`` is caller-held (e.g. ``SITE/artifacts/...``); no filesystem read.
    """
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(sha256, str) or _DIGEST.fullmatch(sha256) is None:
        return None
    if state not in MANIFEST_STATES:
        return None
    return {"path": path, "sha256": sha256, "alg": PROVENANCE_ALG, "validationState": state}


def provenance_manifest_summary(ingredients: list[dict], *, disclosure: str = "standard") -> dict | None:
    """Typed manifest summary over ingredients — c2pa Manifest/Claim shape (C2PA Ch.11/10).

    Returns ``{"alg": "sha256", "disclosureLevel": ..., "ingredients": [...], "ingredientCount": n}``
    when ``disclosure`` is in ``DISCLOSURE_LEVELS`` (minimal/standard/full per
    Ch.16.3 p.111) and each ingredient is a ``provenance_ingredient``-shaped
    dict whose ``sha256`` is hex64 and ``validationState`` is in
    ``MANIFEST_STATES``; else ``None``. No signing, no JUMBF, no network.
    ``minimal`` carries only ``claimSignature.validated + dataHash`` per
    Ch.16.3, ``full`` all ingredient digests — the privacy choice the header
    documents (Ch.1 p.2 SHOULD NOT; Sec.6.8 redactedAssertions analogue).
    """
    if disclosure not in DISCLOSURE_LEVELS:
        return None
    if not isinstance(ingredients, list):
        return None
    for ing in ingredients:
        if not isinstance(ing, dict):
            return None
        sha = ing.get("sha256")
        st = ing.get("validationState")
        if not isinstance(sha, str) or _DIGEST.fullmatch(sha) is None:
            return None
        if not isinstance(st, str) or st not in MANIFEST_STATES:
            return None
    return {"alg": PROVENANCE_ALG, "disclosureLevel": disclosure, "ingredients": list(ingredients), "ingredientCount": len(ingredients)}


def provenance_validation_state(status_codes: list[str]) -> str | None:
    """Map C2PA status codes to ManifestValidationState — Sec.15.2 pp.74-78, Sec.14.3/15.3.

    Codes: ``claimSignature.validated``, ``assertion.dataHash.match``,
    ``signingCredential.trusted`` (success) plus ``ingredient.unknownProvenance``
    (informational) — all from Sec.15.2. ``Valid`` requires
    ``claimSignature.validated`` and ``assertion.dataHash.match``; ``Trusted``
    additionally requires ``signingCredential.trusted`` (whose anchor is
    ``TRUSTED_SIGNERS`` per Sec.14.4 — empty here, so ``Valid`` is the ceiling
    until a trust list is configured). Non-list or malformed element -> ``None``;
    empty list -> ``None``; no success pair -> ``None`` (unknown).
    """
    if not isinstance(status_codes, list) or not status_codes:
        return None
    for code in status_codes:
        if not isinstance(code, str) or not code:
            return None
    has_sig = "claimSignature.validated" in status_codes
    has_hash = "assertion.dataHash.match" in status_codes
    has_trusted = "signingCredential.trusted" in status_codes
    if has_sig and has_hash and has_trusted:
        return "trusted"
    if has_sig and has_hash:
        return "valid"
    return None


def provenance_revocation_status(status_codes: list[str]) -> str:
    """Map C2PA status codes to revocation trichotomy — Golaszewski p.3-4 Fig.2 / Sec.15.6.

    ``signingCredential.revoked`` -> ``"revoked"``;
    ``signingCredential.notRevoked`` or ``signingCredential.trusted``+``ocspGood`` -> ``"not-revoked"``;
    else -> ``"unchecked"`` (spec-legal default that defeats all goals). Non-list -> ``"unchecked"``.
    The word is the detector, not a fetch.
    """
    if not isinstance(status_codes, list):
        return "unchecked"
    for code in status_codes:
        if not isinstance(code, str) or not code:
            return "unchecked"
    if "signingCredential.revoked" in status_codes:
        return "revoked"
    if "signingCredential.notRevoked" in status_codes or "signingCredential.trusted" in status_codes and "ocsp.good" in status_codes:
        return "not-revoked"
    return "unchecked"


def provenance_timestamp_binding_holds(status_codes: list[str]) -> bool:
    """Whether timestamp is bound to claim — Golaszewski p.3 Fig.1 / Sec.15.2.

    True only when a ``timestamp.boundToClaim`` / ``timestampAgreement.validated`` /
    ``claimSignature covers timestamp`` code is present — Fig.1 binding, not mere
    ``timeStamp.trusted``. Non-list or malformed -> ``False``.
    """
    if not isinstance(status_codes, list):
        return False
    for code in status_codes:
        if not isinstance(code, str) or not code:
            return False
    return "timestamp.boundToClaim" in status_codes or "timestampAgreement.validated" in status_codes or "claimSignature.coversTimestamp" in status_codes


def provenance_strong_integrity_holds(status_codes: list[str], *, has_exclusion: bool = False) -> bool:
    """Whether strong file integrity holds — Golaszewski p.4 Fig.3 / Sec.18.5.

    True only when ``assertion.dataHash.match`` is present and no exclusion
    range hides mutable bytes. ``has_exclusion=True`` means an exclusion
    range exists (e.g. GPS/front-matter) so binding is at most ``weak``.
    Non-list or malformed -> ``False``.
    """
    if not isinstance(status_codes, list):
        return False
    for code in status_codes:
        if not isinstance(code, str) or not code:
            return False
    if "assertion.dataHash.match" not in status_codes:
        return False
    return not has_exclusion


def provenance_validators_agree(codes_a: list[str], codes_b: list[str]) -> bool:
    """Whether two validators agree — Golaszewski p.4 Finding 3 / p.6.

    Agree when they map to the same ``manifestState`` and same
    revocation/timestamp/strong-integrity trichotomies; otherwise
    ``validator-disagreement``. Non-list inputs -> ``False``.
    """
    if not isinstance(codes_a, list) or not isinstance(codes_b, list):
        return False
    for code in codes_a + codes_b:
        if not isinstance(code, str) or not code:
            return False
    return (provenance_validation_state(codes_a) == provenance_validation_state(codes_b)
            and provenance_revocation_status(codes_a) == provenance_revocation_status(codes_b)
            and provenance_timestamp_binding_holds(codes_a) == provenance_timestamp_binding_holds(codes_b)
            and provenance_strong_integrity_holds(codes_a, has_exclusion=False) == provenance_strong_integrity_holds(codes_b, has_exclusion=False))


def provenance_is_stripped(manifest_bytes: bytes | None) -> bool:
    """Whether manifest is stripped — AFIP/NIST table: no credential found.

    ``None`` or empty bytes means stripped (``no credential`` != ``invalid``).
    ``"no credential"`` must be distinguished from ``not AI`` / ``not published``.
    """
    return manifest_bytes is None or len(manifest_bytes) == 0


def provenance_disclosure_is_valid(level: object) -> bool:
    """Whether the disclosure level is a C2PA Ch.16.3 value — minimal|standard|full."""
    return isinstance(level, str) and level in DISCLOSURE_LEVELS
