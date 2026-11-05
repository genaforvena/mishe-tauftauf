"""Free-form resident walls and addressed chat; no task ledger or receipt judge.

ICMJE Recommendations for the Conduct, Reporting, Editing, and Publication of
Scholarly Work in Medical Journals, Section II.A Defining the Role of Authors
and Contributors (updated January 2026) —
https://www.icmje.org/recommendations/browse/roles-and-responsibilities/defining-the-role-of-authors-and-contributors.html
+ https://www.icmje.org/icmje-recommendations.pdf (+ §V Use of AI) —
https://www.icmje.org/recommendations/browse/artificial-intelligence/ai-use-by-authors.html
— four ANDED criteria ``substantial-contribution AND drafting-or-critical-review
AND final-approval AND accountability`` (II.A.1 boxed list: "Substantial
contributions to the conception or design of the work; or the acquisition,
analysis, or interpretation of data for the work; AND Drafting the work or
reviewing it critically for important intellectual content; AND Final approval
of the version to be published; AND Agreement to be accountable for all aspects
of the work ..."); ``author`` when all four hold else ``acknowledged-contributor``
when at least one holds else ``unknown`` (II.A.1 all-and-only + II.A.3
"those who do not meet all four ... should be acknowledged");
``ACKNOWLEDGMENT_ONLY_ACTIVITIES`` that alone never qualify — "acquisition of
funding; general supervision ...; writing assistance, technical editing, language
editing, and proofreading" (II.A.3); accountability as "can identify which
co-authors are responsible for specific other parts ... confidence in the
integrity of the contributions" (II.A.1 para.2); corresponding author as "one
individual who takes primary responsibility ... should be available throughout ...
and after publication to respond to critiques ... obtain written permission to be
acknowledged" (II.A.1 para.4); AI-assisted technology: "Chatbots (such as
ChatGPT) should not be listed as authors because they cannot be responsible ..."
and disclosure in acknowledgment (writing assistance) or methods (data collection/
analysis/figure generation) plus cover letter (II.A.4 / §V.A January 2026).

CRediT Contributor Roles Taxonomy — https://credit.niso.org/ +
https://credit.niso.org/contributor-roles-defined/ (55,480 B, 200 2026-11-02) +
per-role https://credit.niso.org/contributor-roles/<slug>/ + ANSI/NISO
Z39.104-2022 + Brand et al. 2015 Learned Publishing 28:151-155
doi:10.1087/20150211 — 14 closed roles ``conceptualization ... writing-review-
editing`` complementary to ICMJE four-criteria authorship; ``Funding acquisition
/ Resources / Supervision / Project administration`` alone → ``acknowledged-
contributor`` under ICMJE; multiple roles per contributor, multiple contributors
per role; machine-readable JATS/NISO intent; verdict-free (role ≠ correctness,
cf. C2PA Ch.1 box p.2).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import math
from pathlib import Path
import re
import shlex
from typing import Final

from .feed import Feed, publication_lock
from .chat_protocol import decode_lifecycle
from .outcome_events import blocking_gaps, classify_outcomes, eligible_evidence_path
from .outcome_publication import OUTCOME_KINDS, decode_supersedes
from .observations import sensor_failed as _sensor_failed, sensor_unknown as _sensor_unknown

WALL_MAX_BYTES = 16384
"""Hard byte bound for one edited wall; walls are bounded prose, unlike chat.log."""
WALL_MAX_LINES = 200
"""Hard line bound for one edited wall; keeps the pane readable."""

OUTCOME_REFUSAL_AUDIT = "logs/outcome-refusal-audit.jsonl"
"""Where a refused outcome publication is recorded.

The writer's refusals are the plant's only direct evidence that the outcome
instrument denied a role; without them a plant-wide denial is indistinguishable
from a plant that produced nothing.
"""

# --- ICMJE authorship vocabulary (ICMJE Recommendations II.A, updated January 2026) ---
ICMJE_AUTHORSHIP_CRITERIA: Final[tuple[str, ...]] = (
    "substantial-contribution",
    "drafting-or-critical-review",
    "final-approval",
    "accountability",
)
"""Four ANDED criteria per ICMJE II.A.1 (substantial-contribution glosses the inner OR over conception/design/acquisition/analysis/interpretation)."""

CONTRIBUTORSHIP_STATUSES: Final[tuple[str, ...]] = ("author", "acknowledged-contributor", "unknown")
"""Contributorship per ICMJE II.A.1 all-and-only + II.A.3 acknowledged."""

ACKNOWLEDGMENT_ONLY_ACTIVITIES: Final[tuple[str, ...]] = (
    "acquisition-of-funding",
    "general-supervision-or-administrative-support",
    "writing-assistance-or-technical-or-language-editing-or-proofreading",
)
"""Activities that alone (without other contributions) do not qualify per ICMJE II.A.3."""

CORRESPONDING_AUTHOR_DUTIES: Final[tuple[str, ...]] = (
    "ensure-administrative-requirements",
    "remain-reachable-in-review",
    "remain-reachable-after-publication",
    "handle-permission-for-acknowledgments",
)
"""Corresponding-author duties per ICMJE II.A.1 para.4 (one reachable individual through review and after publication, permission for acknowledgments)."""

AI_USE_DISCLOSURE_LOCATIONS: Final[tuple[str, ...]] = ("acknowledgment", "methods", "cover-letter")
"""Disclosure locations per ICMJE II.A.4 / §V.A (writing assistance → acknowledgment; data/analysis/figure → methods; always cover-letter-advised)."""

AI_CANNOT_BE_AUTHOR: Final[bool] = True
"""Chatbots should not be listed as authors because they cannot be responsible per ICMJE II.A.4 January 2026."""

# --- CRediT vocabulary (ANSI/NISO Z39.104-2022) ---
CREDIT_ROLES: Final[tuple[str, ...]] = (
    "conceptualization",
    "data-curation",
    "formal-analysis",
    "funding-acquisition",
    "investigation",
    "methodology",
    "project-administration",
    "resources",
    "software",
    "supervision",
    "validation",
    "visualization",
    "writing-original-draft",
    "writing-review-editing",
)
"""14 CRediT roles per https://credit.niso.org/contributor-roles-defined/ (NISO Z39.104-2022)."""

CREDIT_ROLE_DEFINITIONS: Final[dict[str, str]] = {
    "conceptualization": "Ideas; formulation or evolution of overarching research goals and aims.",
    "data-curation": "Management activities to annotate (produce metadata), scrub data and maintain research data (including software code, where it is necessary for interpreting the data itself) for initial use and later re-use.",
    "formal-analysis": "Application of statistical, mathematical, computational, or other formal techniques to analyze or synthesize study data.",
    "funding-acquisition": "Acquisition of the financial support for the project leading to this publication.",
    "investigation": "Conducting a research and investigation process, specifically performing the experiments, or data/evidence collection.",
    "methodology": "Development or design of methodology; creation of models.",
    "project-administration": "Management and coordination responsibility for the research activity planning and execution.",
    "resources": "Provision of study materials, reagents, materials, patients, laboratory samples, animals, instrumentation, computing resources, or other analysis tools.",
    "software": "Programming, software development; designing computer programs; implementation of the computer code and supporting algorithms; testing of existing code components.",
    "supervision": "Oversight and leadership responsibility for the research activity planning and execution, including mentorship external to the core team.",
    "validation": "Verification, whether as a part of the activity or separate, of the overall replication/reproducibility of results/experiments and other research outputs.",
    "visualization": "Preparation, creation and/or presentation of the published work, specifically visualization/data presentation.",
    "writing-original-draft": "Preparation, creation and/or presentation of the published work, specifically writing the initial draft (including substantive translation).",
    "writing-review-editing": "Preparation, creation and/or presentation of the published work by those from the original research group, specifically critical review, commentary or revision \u2013 including pre- or post-publication stages.",
}
"""CRediT definitions verbatim from contributor-roles-defined (per-role pages same)."""

ACKNOWLEDGMENT_ONLY_CREDIT_ROLES: Final[tuple[str, ...]] = (
    "funding-acquisition",
    "resources",
    "project-administration",
    "supervision",
)
"""CRediT roles that alone correspond to ICMJE II.A.3 acknowledgment-only activities."""


def icmje_criterion_is_valid(criterion: object) -> bool:
    """Membership in ICMJE_AUTHORSHIP_CRITERIA."""
    return criterion in ICMJE_AUTHORSHIP_CRITERIA


def contributorship_status_is_valid(status: object) -> bool:
    """Membership in CONTRIBUTORSHIP_STATUSES."""
    return status in CONTRIBUTORSHIP_STATUSES


def acknowledgment_only_activity_is_valid(activity: object) -> bool:
    """Membership in ACKNOWLEDGMENT_ONLY_ACTIVITIES."""
    return activity in ACKNOWLEDGMENT_ONLY_ACTIVITIES


def ai_disclosure_location_is_valid(location: object) -> bool:
    """Membership in AI_USE_DISCLOSURE_LOCATIONS."""
    return location in AI_USE_DISCLOSURE_LOCATIONS


def authorship_criteria_met(
    *, substantial: bool, drafting_or_review: bool, final_approval: bool, accountability: bool
) -> bool:
    """Four-way AND per ICMJE II.A.1 — True only when all four hold (3 of 4 is not author)."""
    return bool(substantial and drafting_or_review and final_approval and accountability)


def contributorship_status_for(
    *, substantial: bool, drafting_or_review: bool, final_approval: bool, accountability: bool
) -> str:
    """Map four booleans -> author if all four else acknowledged-contributor if at least one else unknown."""
    if authorship_criteria_met(
        substantial=substantial,
        drafting_or_review=drafting_or_review,
        final_approval=final_approval,
        accountability=accountability,
    ):
        return "author"
    if any((substantial, drafting_or_review, final_approval, accountability)):
        return "acknowledged-contributor"
    return "unknown"


def accountability_holds(*, can_identify_parts: bool, confidence_in_coauthors: bool) -> bool:
    """Both required per ICMJE II.A.1 para.2 (identifiability + confidence)."""
    return bool(can_identify_parts and confidence_in_coauthors)


def corresponding_author_is_responsible(*, corresponding: str, authors: list[str]) -> bool:
    """One non-empty non-whitespace single role present in authors list (the one individual rule)."""
    if not isinstance(corresponding, str) or not corresponding:
        return False
    for ch in corresponding:
        if ch.isspace():
            return False
    if not isinstance(authors, (list, tuple)):
        return False
    return corresponding in authors


def is_ai_author_allowed() -> bool:
    """Chatbots should not be listed as authors per ICMJE II.A.4 — always False."""
    return False


def ai_disclosure_location_for(kind: str) -> str | None:
    """Map AI use kind to disclosure location (writing-assistance -> acknowledgment, data/analysis/figure -> methods)."""
    if kind == "writing-assistance":
        return "acknowledgment"
    if kind == "data-collection-or-analysis-or-figure-generation":
        return "methods"
    return None


def credit_role_is_valid(role: object) -> bool:
    """Membership in CREDIT_ROLES."""
    return role in CREDIT_ROLES


def credit_role_definition(role: str) -> str | None:
    """Definition for a CRediT role or None."""
    return CREDIT_ROLE_DEFINITIONS.get(role)


def normalize_credit_role(value: object) -> str | None:
    """Normalize a CRediT role display name to its hyphen slug (e.g. Writing - review & editing -> writing-review-editing)."""
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    out: list[str] = []
    prev_hyphen = False
    for ch in lowered:
        is_alnum = ("a" <= ch <= "z") or ("0" <= ch <= "9")
        if is_alnum:
            out.append(ch)
            prev_hyphen = False
        else:
            if not prev_hyphen and out:
                out.append("-")
                prev_hyphen = True
            elif not out:
                # leading non-alnum -> skip (trims leading hyphens)
                prev_hyphen = True
            else:
                prev_hyphen = True
    if out and out[-1] == "-":
        out.pop()
    normalized = "".join(out)
    return normalized if normalized else None

def credit_roles_are_valid(roles: object) -> bool:
    """Non-empty iterable of valid CRediT roles with no duplicates."""
    if not isinstance(roles, (list, tuple)):
        return False
    if not roles:
        return False
    seen: set[str] = set()
    for r in roles:
        if not isinstance(r, str) or r not in CREDIT_ROLES:
            return False
        if r in seen:
            return False
        seen.add(r)
    return True


def contribution_is_acknowledgment_only(roles: list[str]) -> bool:
    """True when every supplied CRediT role is in ACKNOWLEDGMENT_ONLY_CREDIT_ROLES (ICMJE II.A.3)."""
    if not isinstance(roles, (list, tuple)) or not roles:
        return False
    for r in roles:
        if r not in ACKNOWLEDGMENT_ONLY_CREDIT_ROLES:
            return False
    return True



class BoundSetUnavailable(ValueError):
    """The bound evidence set cannot be derived, so a publication fails closed.

    The gaps that block the derivation are carried, so a refusal can name the
    tape entry responsible instead of leaving the reader to guess.
    """

    def __init__(self, reason: str, gaps=None):
        super().__init__(reason)
        self.gaps = list(gaps or [])


def _audit_outcome_refusal(home: Path, role: str, kind: str, reason: str, gaps,
                           evidence: str | None = None, resolved: str | None = None) -> None:
    """Append a refused outcome publication so the denial is observable.

    ``evidence`` is the evidence argument the caller named and ``resolved`` the
    path the resolver actually checked, so a path-form refusal can be read
    without reconstructing the cwd. Neither carries file content. Never raises:
    an audit write must not break the refusal it records.
    """
    try:
        logs_dir = home / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "role": role, "kind": kind,
                 "reason": reason, "gaps": list(gaps), "evidence": evidence,
                 "resolved": resolved,
                 "pid": os.getpid(), "uid": os.geteuid()}
        with open(logs_dir / "outcome-refusal-audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _extract_move_id(text: str | None) -> int | None:
    """Parse explicit handoff token ``move:<digest>:from=<role>:id=<lamport>``."""
    if not isinstance(text, str) or not text:
        return None
    # Slicing only so discovery/wall stay outside parsing boundary
    needle = "move:"
    idx = text.find(needle)
    while idx >= 0:
        # expect move:<64 hex>:from=<role>:id=<digits>
        hex_start = idx + len(needle)
        hex_end = hex_start + 64
        if hex_end <= len(text) and all(c in "0123456789abcdef" for c in text[hex_start:hex_end]):
            rest = text[hex_end:]
            if rest[0:6] == ":from=":
                from_end = rest.find(":id=")
                if from_end >= 0:
                    digits = rest[from_end + 4:]
                    num = ""
                    for ch in digits:
                        if ch.isdigit():
                            num += ch
                        else:
                            break
                    if num:
                        try:
                            return int(num)
                        except ValueError:
                            pass
        idx = text.find(needle, idx + 1)
    return None


def move_winner(entries: list[dict]) -> dict[str, int]:
    """Per-digest winner map (Alg. 1:5, Alg. 2:4) — largest Lamport ID wins."""
    winners: dict[str, int] = {}
    for idx, entry in enumerate(entries):
        digest = entry.get("digest")
        if not isinstance(digest, str) or not digest:
            continue
        seq = entry.get("move_id")
        if not isinstance(seq, int):
            seq = entry.get("seq")
        if not isinstance(seq, int):
            seq = entry.get("sequence")
        if not isinstance(seq, int):
            seq = idx
        cur = winners.get(digest)
        if cur is None or seq > cur:
            winners[digest] = seq
    return winners


def is_ancestor(tree: dict, ancestor: object, descendant: object) -> bool:
    """Walk ``tree[child]=parent`` to decide ancestor ancestor-of descendant (Alg. 1:30)."""
    if ancestor is None or descendant is None or ancestor == descendant:
        return False
    cur = tree.get(descendant) if isinstance(tree, dict) else None
    seen: set[object] = set()
    for _ in range(10000):
        if cur is None:
            return False
        if cur == ancestor:
            return True
        if cur in seen:
            return False
        seen.add(cur)
        cur = tree.get(cur) if isinstance(tree, dict) else None
    return False


def lifecycle_lists(entries: list[dict]) -> dict[str, dict[str, list[int]]]:
    """Per-digest PresentList/TrashList (§3.3.2) for lifecycle bypass."""
    present: dict[str, list[int]] = {}
    last_by_role: dict[str, tuple[str, int]] = {}
    trash: dict[str, list[int]] = {}
    for idx, entry in enumerate(entries):
        role = entry.get("role")
        digest = entry.get("digest")
        if not isinstance(role, str) or not isinstance(digest, str) or not digest:
            continue
        seq = entry.get("move_id")
        if not isinstance(seq, int):
            seq = entry.get("seq")
        if not isinstance(seq, int):
            seq = entry.get("sequence")
        if not isinstance(seq, int):
            seq = idx
        present.setdefault(digest, []).append(seq)
        prev = last_by_role.get(role)
        if prev is not None and prev[0] != digest:
            trash.setdefault(prev[0], []).append(seq)
        last_by_role[role] = (digest, seq)
    all_digests = set(present) | set(trash)
    result: dict[str, dict[str, list[int]]] = {}
    for d in all_digests:
        result[d] = {"present": sorted(present.get(d, [])), "trash": sorted(trash.get(d, []))}
    return result


def _audit_log(home: Path, role: str, source_path: str | None, wall_path: Path, digest: str, text: str | None = None) -> None:
    """Append a wall-write audit entry so clobbers are diagnosable after the fact.

    Logs: timestamp, role, source notes path (if known), destination wall path,
    post-write mtime, the written document's digest (trailing newlines
    normalized, matching ``wall_text.is_peer_copy``), and per-write ``parents``
    (object\u2192old parent for RAR suffix recompute, Alg. 2:9) plus explicit
    ``move_id`` when the write carries a handoff token. The digest lets the
    peer-copy guard refuse a paste of another role's *superseded* wall, which
    no current wall file can show. Writes to ``logs/wall-write-audit.jsonl``
    under the ignored site. Never raises — audit logging must not break wall
    delivery.
    """
    try:
        logs_dir = home / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "source": str(source_path) if source_path else None,
            "wall": str(wall_path),
            "mtime": datetime.fromtimestamp(wall_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "digest": digest,
            "move_id": _extract_move_id(text),
            "parents": {},
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "uid": os.geteuid(),
        }
        with open(logs_dir / "wall-write-audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass

def _audit_refusal(home: Path, role: str, source_path: str | None, wall_path: Path, reason: str) -> None:
    """Append a wall-write refusal audit entry so refusals are diagnosable after the fact.

    Logs: timestamp, role, source notes path (if known), destination wall path,
    both mtimes, and the refusal reason. Writes to ``logs/wall-write-audit.jsonl``
    under the ignored site. Never raises — audit logging must not break wall
    delivery.
    """
    try:
        logs_dir = home / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        source_mtime = None
        if source_path and Path(source_path).is_file():
            source_mtime = datetime.fromtimestamp(
                Path(source_path).stat().st_mtime, tz=timezone.utc
            ).isoformat()
        wall_mtime = None
        if wall_path.is_file():
            wall_mtime = datetime.fromtimestamp(
                wall_path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "source": str(source_path) if source_path else None,
            "source_mtime": source_mtime,
            "wall": str(wall_path),
            "wall_mtime": wall_mtime,
            "reason": reason,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "uid": os.geteuid(),
        }
        with open(logs_dir / "wall-write-audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _bound_evidence_paths(home: Path, *, entries=None) -> set[str]:
    """Derive post-clause event bindings, failing closed on incomplete coverage.

    A gap fails closed only while it can still hide a bound evidence path: a
    reserved header or an immutable reference marks a publication attempt. A
    damaged entry that carries neither binds nothing, so it is reported as a
    coverage gap but cannot block another role's publication.

    ``entries`` reuses a caller's tape snapshot without rereading it.
    """
    from .discovery import EVIDENCE_BINDING_CLAUSE_TIME
    coverage = classify_outcomes(home, EVIDENCE_BINDING_CLAUSE_TIME, entries=entries)
    if coverage["unavailable"] == "chat tape unavailable" and not (home / "chat.log").exists():
        return set()
    if coverage["unavailable"]:
        raise BoundSetUnavailable("cannot derive bound evidence paths: " + str(coverage["unavailable"]))
    blocking = blocking_gaps(coverage)
    if blocking:
        raise BoundSetUnavailable("cannot derive bound evidence paths: " + str(blocking),
                                  gaps=blocking)
    return {row["evidence"]["path"] for row in coverage["events"]}


def outcome(home: Path, role: str, kind: str, text: str, evidence: Path,
            supersedes: list[str] | None = None):
    """Record an evidence-backed contribution, not a task transition or acceptance gate.

    ``supersedes`` names the record digests of earlier outcomes this
    publication re-evidences. The link is carried in the record payload's
    structured field, so the plant's work-identity relation is decidable
    without reading prose.
    """
    from .observations import validate_slug
    validate_slug(role)
    if kind not in OUTCOME_KINDS or not text.strip():
        raise ValueError("outcome requires a supported kind and nonempty prose")
    try:
        return _publish_outcome(home, role, kind, text, evidence, supersedes)
    except ValueError as exc:
        _audit_outcome_refusal(home, role, kind, str(exc), getattr(exc, "gaps", []),
                               evidence=str(evidence),
                               resolved=_resolved_form_for_audit(home, evidence))
        raise


def _resolved_form_for_audit(home: Path, evidence: Path) -> str | None:
    """The resolver's chosen path for a refused evidence argument, or None.

    Audit-only: a resolution failure here must not replace the refusal reason.
    """
    try:
        return str(resolve_outcome_evidence(home, evidence))
    except Exception:
        return None


def resolve_outcome_evidence(home: Path, evidence: Path) -> Path:
    """Resolve a relative outcome evidence argument to its eligible site file.

    A relative path names the cwd form first, the historical rule. When that form
    is not an eligible site path, the home-relative form is used instead, so a
    bare ``artifacts/NAME.md`` run from the repository root still names the owned
    site file. Neither form is preferred when the cwd form is already eligible,
    so no path accepted before this rule changes meaning.
    """
    if evidence.is_absolute():
        return evidence
    cwd_form = Path.cwd() / evidence
    if eligible_evidence_path(home, cwd_form):
        return cwd_form
    home_form = Path(home) / evidence
    if eligible_evidence_path(home, home_form) and home_form.is_file():
        return home_form
    return cwd_form


def _publish_outcome(home: Path, role: str, kind: str, text: str, evidence: Path,
                     supersedes: list[str] | None = None):
    original_evidence = resolve_outcome_evidence(home, evidence)
    if not eligible_evidence_path(home, original_evidence):
        raise ValueError(
            "outcome evidence must be an eligible single file under artifacts/, "
            "discovery/scan-*.json or dms/*.md; use a flat artifacts/NAME file "
            "(nested artifacts/<role>/<file> is refused); symlinks and traversal are forbidden"
            f" (resolved to {original_evidence})")
    evidence = original_evidence.resolve()
    if not evidence.is_file():
        raise ValueError("outcome evidence must be an existing file")
    evidence_str = str(evidence)
    # The supersession link is a structured field, so it is decoded rather than
    # read out of prose. A malformed digest is refused here rather than
    # published as a link that names nothing.
    linked = decode_supersedes(supersedes)
    if supersedes and len(linked) != len(supersedes):
        raise ValueError("outcome supersedes must name record digests")
    # Serialize the duplicate-path check, evidence snapshot, mode change and
    # publication. Later edits remain detectable by the observer; read-only
    # mode prevents accidental edits by compliant writers.
    # One streaming parse of the tape feeds the bound check so publication
    # never re-parses the tape per candidate and never holds the whole tape
    # as a byte string plus entry list.
    with publication_lock(home):
        tape = home / "chat.log"
        if tape.exists():
            from .outcome_events import parse_feed
            entries = parse_feed(tape, home=home)
        else:
            entries = None
        try:
            bound = _bound_evidence_paths(home, entries=entries)
        except TypeError:
            bound = _bound_evidence_paths(home)
        if evidence_str in bound:
            raise ValueError(
                f"outcome evidence path already bound: {evidence_str}; each outcome binds its own "
                "flat artifacts/NAME file (bind a copy of the final bytes), or re-evidence with "
                "--supersedes <record digest>")
        data = evidence.read_bytes()
        if not data:
            raise ValueError("outcome evidence must not be empty")
        payload = {"role": role, "kind": kind, "text": text.strip(),
                   "evidence": {"path": str(evidence), "sha256": hashlib.sha256(data).hexdigest()}}
        if linked:
            payload["supersedes"] = linked
        original_mode = evidence.stat().st_mode & 0o7777
        os.chmod(evidence, original_mode & ~0o222)
        try:
            return Feed(home).append_record(role, f"Wall outcome {kind} by {role}\n{text.strip()}",
                payload, kind="wall-outcome")
        except BaseException:
            try:
                os.chmod(evidence, original_mode)
            except OSError as restore_error:
                raise RuntimeError(
                    f"outcome publication failed and evidence mode restoration failed: {restore_error}"
                ) from restore_error
            raise




def settings(home: Path) -> dict:
    path = home / "coordination-mode.json"
    if not path.exists():
        return {"mode": "wall"}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("settings must be an object")
        if data.get("mode", "wall") != "wall":
            raise ValueError("ledger coordination is retired; migrate this site explicitly before activation")
        data["mode"] = "wall"
        if data.get("until"):
            deadline = datetime.fromisoformat(data["until"].replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                raise ValueError("deadline needs timezone")
        return data
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"coordination mode unavailable: {exc}") from exc


def enabled(home: Path) -> bool:
    settings(home)
    return True


def limits(home: Path) -> tuple[int, int]:
    """The hard wall bound; `coordination-mode.json` may tune it."""
    config = settings(home)
    chosen = []
    for key, default in (("wall_max_bytes", WALL_MAX_BYTES), ("wall_max_lines", WALL_MAX_LINES)):
        value = config.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"coordination mode {key} must be a positive integer")
        chosen.append(value)
    return chosen[0], chosen[1]


def write(home: Path, role: str, text: str, source_path: str | None = None) -> None:
    from .observations import validate_home, validate_slug
    from .state_files import write_text_atomic as _write_handoff
    validate_home(home)
    validate_slug(role)
    max_bytes, max_lines = limits(home)
    size, lines = len(text.encode("utf-8")), len(text.splitlines())
    exceeded = [f"{size} bytes (limit {max_bytes})"] if size > max_bytes else []
    if lines > max_lines:
        exceeded.append(f"{lines} lines (limit {max_lines})")
    if exceeded:
        raise ValueError(
            f"wall {role} exceeds its size limit: {'; '.join(exceeded)}. A wall is a short, "
            "current document, not an append-only log — cut stale detail and rewrite it down "
            "so superseded claims and past mistakes do not mislead the next reader. Raise "
            "wall_max_bytes/wall_max_lines in coordination-mode.json only when the bound is "
            "genuinely too small."
        )
    wall_path = home / "walls" / f"{role}.md"
    from .wall_text import document_digest
    digest = document_digest(text)
    _reject_peer_wall_copy(home, role, text, digest, source_path)
    _write_handoff(wall_path, text)
    # handoffs/<role>.md is the launcher-facing projection of these same notes:
    # runtime.py:_context reads it as CURRENT HANDOFF and wall.settle's
    # already-settled branch compares both files against the settle text.
    # Writing only the wall left the projection at the previous wake's content,
    # so a mind that edited its wall directly handed the next invocation a stale
    # handoff and a same-wake re-settle failed the file match for good
    # (android-body genome wake 35129 stuck pending 07:23-08:03Z 2026-10-11,
    # repaired by hand at 08:03Z; body-research wake 8405 reproduces the class in
    # artifacts/body-wake8405-handoff-desync-repro.json). Project it here so the
    # two files cannot diverge; _write_handoff skips an identical rewrite.
    _write_handoff(home / "handoffs" / f"{role}.md", text)
    _audit_log(home, role, source_path, wall_path, digest, text)


def _reject_peer_wall_copy(home: Path, role: str, text: str, digest: str,
                           source_path: str | None = None) -> None:
    """Reject notes identical to another role's wall.

    A restore/context document renders every peer wall undelimited into one
    text. A mind that pastes a peer section into its own notes produces a
    cross-role wall, which ``wall write``/``seed yield`` then accept with no
    ownership cross-check — the class that clobbered three walls with
    research-methods' content on 2026-10-10 22:01-22:23Z. Cross-role duplication
    has no legitimate use under per-role wall ownership; rewriting a wall with
    unchanged own content is unaffected. Comparison ignores trailing newlines,
    so the paste class cannot slip through by ending on a different blank-line
    count; any other difference still reads as the writer's own.

    The live-wall comparison sees only each peer's current document, so a paste
    of a peer's *superseded* wall — the class that put witness content on
    android-body's discover wall on 2026-10-11 07:5xZ — matches nothing current
    and used to pass. The write audit records a digest per accepted write, so
    the same document is also refused when any other role wrote it earlier.
    A refusal is recorded through ``_audit_refusal``: the paste class clobbered
    three walls on 2026-10-10 and one on 2026-10-11, and the caller's pane was
    its only trace, so recurrence had no durable record.
    """
    from .roles import declared as roles
    from .wall_text import is_peer_copy
    peers = set(roles(home)) - {role}
    if role != "operator" and (home / "walls" / "operator.md").is_file():
        peers.add("operator")
    for name in sorted(peers):
        path = home / "walls" / f"{name}.md"
        if path.is_file() and is_peer_copy(text, path.read_bytes()):
            _audit_refusal(home, role, source_path, home / "walls" / f"{role}.md", "peer-copy")
            raise ValueError(
                f"refusing to write wall {role}: notes are identical to peer wall {name}. "
                "The restore document renders every role's wall in one text — copy from your "
                "own wall section only, or rewrite your current plan from your own evidence. "
                f"If {name} needs this content, {name} writes it."
            )
    _reject_peer_history_copy(home, role, digest, source_path, text)


_AUDIT_HISTORY_LIMIT = 4000
"""How many recent audit entries the history guard reads; bounded so a write stays cheap."""


def _recent_audit_entries(home: Path) -> list[dict]:
    """Tail of the wall-write audit, oldest first; a missing log reads empty.

    Decoding the log's text form is the wall-text boundary's job; this only
    reads bytes and bounds how much history one write consults.
    """
    from .wall_text import decode_audit_entries
    try:
        text = (home / "logs" / "wall-write-audit.jsonl").read_text(encoding="utf-8")
    except OSError:
        return []
    return decode_audit_entries(text)[-_AUDIT_HISTORY_LIMIT:]


def _reject_peer_history_copy(home: Path, role: str, digest: str,
                              source_path: str | None = None, text: str | None = None) -> None:
    """Refuse notes whose digest matches a document another role wrote earlier.

    The live-wall guard cannot see a peer wall that has since been replaced, so
    the audit history is the continuing observation of that failure class. Own
    rewrites of unchanged content match the writer's own earlier digests and are
    unaffected; blank notes carry no document to confuse and are skipped.
    A refusal here is recorded through ``_audit_refusal``, so a recurrence of
    the superseded-wall paste is countable rather than pane-only.

    Lifecycle bypass (Alg. 2:26 / \u00a73.3.2): scalar-like write (digest not present
    for any peer) needs no scan. Same-digest concurrent moves ranked by Lamport
    ID (largest wins); RAR parents preserved in audit for suffix recompute.
    Cycle guard via IsAncestor.
    """
    entries = _recent_audit_entries(home)
    if not digest:
        return
    peer_entries = [e for e in entries if e.get("role") != role and isinstance(e.get("digest"), str) and e.get("digest")]
    if not peer_entries:
        return
    _ll = lifecycle_lists(peer_entries)
    _present = _ll.get(digest, {}).get("present")
    if not _present:
        return
    cur_id = _extract_move_id(text)
    if cur_id is None:
        cur_id = len(entries)
    winner_map = move_winner(entries)
    winner_id = winner_map.get(digest)
    if winner_id is not None and cur_id is not None and winner_id > cur_id:
        _audit_refusal(home, role, source_path, home / "walls" / f"{role}.md", "peer-copy-history")
        raise ValueError(
            f"refusing to write wall {role}: notes are identical to a document another role wrote \u2014 concurrent same-digest move, winner has larger Lamport ID ({winner_id} > {cur_id}). "
            "The restore document and every peer wall share one undelimited text — "
            "copy from your own wall section only, or rewrite your current plan from "
            "your own evidence."
        )
    try:
        tree: dict = {}
        last_by_role: dict[str, str] = {}
        for e in entries:
            r, d = e.get("role"), e.get("digest")
            if isinstance(r, str) and isinstance(d, str) and d:
                if r in last_by_role:
                    tree[d] = last_by_role[r]
                last_by_role[r] = d
        if is_ancestor(tree, digest, digest):
            _audit_refusal(home, role, source_path, home / "walls" / f"{role}.md", "peer-copy-cycle")
            raise ValueError(
                f"refusing to write wall {role}: move would create a cycle (IsAncestor)."
            )
    except ValueError:
        raise
    except Exception:
        pass
    for entry in reversed(entries):
        if entry.get("role") == role or not entry.get("digest"):
            continue
        if entry["digest"] == digest:
            is_move = _extract_move_id(text) is not None or any(isinstance(e.get("move_id"), int) for e in entries if e.get("digest") == digest)
            if not is_move:
                _audit_refusal(home, role, source_path, home / "walls" / f"{role}.md", "peer-copy-history")
                raise ValueError(
                    f"refusing to write wall {role}: notes are identical to a document "
                    f"{entry['role']} wrote at {entry.get('ts', 'an unrecorded time')}. "
                    "The restore document and every peer wall share one undelimited text — "
                    "copy from your own wall section only, or rewrite your current plan from "
                    "your own evidence. If that role needs this content, it writes it."
                )
            if winner_id is not None and cur_id is not None and cur_id >= winner_id:
                return
            _audit_refusal(home, role, source_path, home / "walls" / f"{role}.md", "peer-copy-history")
            raise ValueError(
                f"refusing to write wall {role}: notes are identical to a document "
                f"{entry['role']} wrote at {entry.get('ts', 'an unrecorded time')}. "
                "The restore document and every peer wall share one undelimited text — "
                "copy from your own wall section only, or rewrite your current plan from "
                "your own evidence. If that role needs this content, it writes it."
            )


def message(home: Path, source: str, recipient: str, text: str):
    from .observations import validate_slug
    validate_slug(recipient)
    if not text.strip():
        raise ValueError("message is empty")
    entry = Feed(home).append(source, f"[dm] to={recipient}\n{text}")
    from .owed import touch_inbox
    touch_inbox(home, recipient)         # the tape is the record; this is only the trigger
    return entry




def dm_recipient(text: str, source: str) -> str | None:
    """The recipient of an addressed ``[dm] to=ROLE`` first line, or None.

    Mirrors ``addressed``: the first line must be exactly ``[dm] to=ROLE`` with a
    valid slug, and the sender must not address itself. Used by ``append`` so an
    addressed DM written through the plain CLI wakes its recipient like ``wall dm``.
    """
    from .observations import validate_slug
    first = text.splitlines()[0] if text else ""
    match = re.fullmatch(r"\[dm\] to=(\S+)", first)
    if match is None or match.group(1) in {source, f"mind/{source}"}:
        return None
    try:
        validate_slug(match.group(1))
    except ValueError:
        return None
    return match.group(1)


def addressed(entry, role: str) -> bool:
    first = entry.body.splitlines()[0] if entry.body else ""
    if entry.source == "operator/permissions":
        decision = re.match(r"\[permission\] id=\S+ decision=(?:granted|revoked) owner=([A-Za-z0-9._:/-]+)(?:\s|$)", first)
        if decision:
            return decision.group(1) == role
    return entry.source not in {role, f"mind/{role}"} and first == f"[dm] to={role}"


def context(home: Path, role: str) -> str:
    from .observations import validate_slug
    from .roles import declared as roles
    validate_slug(role)
    sections = []
    own = home / "walls" / f"{role}.md"
    sections.append(f"YOUR WALL ({own})\n" + (own.read_text() if own.exists() else "(empty; write your plan here)"))
    peers = set(roles(home) - {role})
    if role != "operator" and (home / "walls" / "operator.md").is_file():
        peers.add("operator")
    for name in sorted(peers):
        path = home / "walls" / f"{name}.md"
        if path.is_file():
            sections.append(f"PEER WALL {name} — reference only; never copy into your own wall\n"
                            f"{path.read_text()}")
    entries = Feed(home).role_entries(role)
    inbox = [e for e in entries if addressed(e, role)][-12:]
    sections.append("ADDRESSED MESSAGES (shared tape, not private)\n" + "\n".join(
        f"{e.sequence} {e.source}: {e.body}" for e in inbox))
    sections.append("RECENT CHAT\n" + "\n".join(
        f"{e.sequence} {e.source}: {e.body}" for e in [e for e in entries if e.source != "seed"][-12:]))
    return "\n\n".join(sections)


def _wall_provenance_line(home: Path, role: str) -> str:
    """Passive document-based margin for the MIND WALL block (Dourish & Bellotti §5.2).

    Derived from the existing ``logs/wall-write-audit.jsonl`` tail and the
    wall file's own mtime/digest, not from a new writer field. Reads as
    UNKNOWN when the file or audit entry is absent so the absence is visible
    rather than silent. Placed inside the MIND WALL block so the OBSERVATION
    digest (split at that header) stays byte-identical for unchanged sensors.
    """
    from .wall_text import decode_audit_entries, document_digest
    from .timestamp_text import decode_iso_datetime

    wall_path = home / "walls" / f"{role}.md"
    if not wall_path.is_file():
        return f"WALL PROVENANCE: walls/{role}.md — UNKNOWN (no file)"
    try:
        text = wall_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"WALL PROVENANCE: walls/{role}.md — UNKNOWN (unreadable: {exc})"
    try:
        mtime = datetime.fromtimestamp(wall_path.stat().st_mtime, tz=timezone.utc)
        mtime_iso = mtime.isoformat().replace("+00:00", "Z")
    except OSError as exc:
        mtime_iso = f"UNKNOWN ({exc})"
        mtime = None
    digest = document_digest(text)
    short = digest[:12]
    try:
        audit_text = (home / "logs" / "wall-write-audit.jsonl").read_text(encoding="utf-8")
        entries = decode_audit_entries(audit_text)
    except OSError:
        entries = []
    matching = [e for e in entries if e.get("role") == role and e.get("digest") == digest]
    if matching:
        entry = matching[-1]
        ts_str = entry.get("ts") or entry.get("mtime") or "unknown"
        moment = decode_iso_datetime(ts_str)
        if moment is None:
            age_str = "unknown age"
        else:
            age_s = (datetime.now(timezone.utc) - moment).total_seconds()
            if age_s < 0:
                age_str = f"{age_s:.0f}s (future)"
            elif age_s < 60:
                age_str = f"{age_s:.0f}s"
            elif age_s < 3600:
                age_str = f"{age_s/60:.1f}m"
            elif age_s < 86400:
                age_str = f"{age_s/3600:.1f}h"
            else:
                age_str = f"{age_s/86400:.1f}d"
        return f"WALL PROVENANCE: walls/{role}.md @ {mtime_iso} digest {short} age {age_str} (audit {ts_str}) — persistent record"
    return f"WALL PROVENANCE: walls/{role}.md @ {mtime_iso} digest {short} — UNKNOWN (no audit entry for this digest)"


def wall_skeleton_line(path: Path) -> str:
    """Presentation only: which common headings a wall carries. Never gates admission or content."""
    from . import wall_text
    if not path.exists():
        return "WALL SKELETON: UNKNOWN (no file)"
    text = path.read_text()

    def present(heading: str) -> str:
        return "yes" if wall_text.heading_present(text, heading) else "no"

    return f"WALL SKELETON: plate={present('plate')} blockers={present('blockers?')} next={present('next')}"




def mind_block(home: Path, role: str) -> str:
    """The role's block of the evidence view: its edited wall, last wake, mail and recent chat."""
    path = home / "walls" / f"{role}.md"
    text = path.read_text() if path.exists() else "(empty; mind may plan here)\n"
    from . import wall_text
    from .core.state import DispatchStore
    shared = Feed(home).role_entries(role)
    entries = [e for e in shared if addressed(e, role)][-3:]
    recent = [e for e in shared if e.source != "seed"][-8:]
    tape = Feed(home).tail_sequence()
    chat = (f"\nCHAT.LOG — last {len(recent)} of {tape} tape events shown (seed included; non-seed total not counted); "
            "full tape: " + str(home / "chat.log") + "\n")
    chat += "\n".join(f"{e.sequence} {e.source}: " +
                      (" ".join(e.body.split())[:360] + (" …" if len(" ".join(e.body.split())) > 360 else ""))
                      for e in recent) or "(no conversation yet)"
    record = DispatchStore(home / "dispatch").read(role)
    wake = ("WAKE: none yet" if record is None else
            f"WAKE: {record.state.name} {record.key} attempt {record.attempt}" + (f" — {record.detail}" if record.detail else ""))
    skeleton = wall_skeleton_line(path)
    provenance = _wall_provenance_line(home, role)
    messages_header = (f"\nMESSAGES — last {len(entries)} addressed shown "
                       f"(addressed total not counted; tape {tape} events)\n")
    return ("\nMIND WALL — edited notes; sensor truth is above\n" + provenance + "\n" + wake + "\n" + text
            + ("" if wall_text.ends_with_newline(text) else "\n") + skeleton + "\n" + messages_header
            + "\n".join(f"{e.sequence} {e.source}: {e.body}" for e in entries) + "\n" + chat + "\n")


def docs_document(home: Path) -> Path:
    """Resolve the reader document inside the repository containing this plant."""
    repository = home.resolve().parent
    selected = settings(home).get("docs_document", "README.md")
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("docs_document must name a document inside the repository")
    relative = Path(selected)
    document = (repository / relative).resolve()
    if relative.is_absolute() or not document.is_relative_to(repository):
        raise ValueError("docs_document must stay inside the planted repository")
    return document




def reject_stale_staging_anchor(home: Path, role: str, text: str, source_path: Path | None,
                                source_is_anchor: bool) -> None:
    """Refuse a write whose staging anchor would silently regress the wall.

    ``wall write --file NOTES`` and ``seed yield --file NOTES`` resolve a bare
    name to the role's own anchor ``SITE_HOME/walls/<role>-NOTES`` (the resolver
    in ``cli`` names the anchor; ``wall`` never re-derives it from the path
    text). An anchor left un-consumed by an earlier wake is then read as this
    wake's plate: the write overwrites the newer wall with the stale anchor and
    reports success, so the plate regresses and the wake's findings are lost
    (class: android-body discover, wall reverted to a stale draft 2026-10-14,
    reproduced in ``artifacts/discover-wake39796-anchor-clobber-repro.py``;
    witness plate reverted to a wake 56136 draft on 2026-10-16 from an anchor
    last staged at 02:10Z, reproduced in
    ``artifacts/witness-wake58696-anchor-clobber-repro.py``). The anchor is only
    trusted when it is at least as new as the wall or already agrees with it;
    otherwise the write fails closed with the remedy, and an explicit ``--file``
    path (which the resolver never calls an anchor) stays the caller's
    deliberate choice.
    """
    if not source_is_anchor or source_path is None:
        return
    source = Path(source_path)
    wall_path = home / "walls" / f"{role}.md"
    if not (source.is_file() and wall_path.is_file()):
        return
    if wall_path.read_text(encoding="utf-8") == text:
        return
    if source.stat().st_mtime > wall_path.stat().st_mtime:
        return
    _audit_refusal(home, role, source_path, wall_path, "stale-staging-anchor")
    raise ValueError(
        f"staged notes {source} are older than the wall {wall_path} and differ from it; "
        f"writing them would regress the wall. Re-stage your current plate into {source.name} "
        "or pass an explicit --file path."
    )








CLEAR_CAPACITY_MAX_AGE = 300






RETRY_HOLD_SECONDS = 1800
# --- Transactional outbox vocabulary (Richardson microservices.io) — Context/Problem/Forces/Solution/Result ---
# UNACTIVATED: no new table, no relay process, no 2PC — vocabulary only, citing
# https://microservices.io/patterns/data/transactional-outbox.html
# Context: service must update aggregate AND publish event. Forces: 2PC not viable,
# commit => must send, rollback => must not send, ordering T1->E1 before T2->E2
# across instances. Solution: Sender writes to Message outbox inside the same
# DB transaction as aggregate update; Message relay later publishes to broker.
# Result: no 2PC, guaranteed send iff commit, ordered. Drawback: forgetting to
# outbox after update. File-system analogue: aggregate = evidence snapshot+mode,
# outbox = durable intent row, relay = recovery_io-style outbox scan.
TRANSACTIONAL_OUTBOX_STATES: Final[tuple[str, ...]] = ("pending", "sent", "failed")
"""Outbox row lifecycle per Richardson Solution — pending (written with aggregate) -> sent (relay published) -> failed (relay gave up). Distinct from Wall outcome kinds; this is the delivery attempt, not the illocutionary force."""

MESSAGE_RELAY_MODES: Final[tuple[str, ...]] = ("polling", "log-tailing")
"""Relay implementation choices per Richardson Implementation notes — polling the outbox table vs tailing the transaction log (file analogue: polling outbox/*.json vs tailing chat.log). No behavior change here."""

OUTBOX_ORDERING: Final[str] = "send-order"
"""Ordering guarantee per Richardson Forces — T1->E1 before T2->E2 across instances updating same aggregate, preserved by relay in send order."""


def outbox_state_is_valid(value: object) -> bool:
    """Whether *value* is a TRANSACTIONAL_OUTBOX_STATES member."""
    return isinstance(value, str) and value in TRANSACTIONAL_OUTBOX_STATES


def relay_mode_is_valid(value: object) -> bool:
    """Whether *value* is a MESSAGE_RELAY_MODES member."""
    return isinstance(value, str) and value in MESSAGE_RELAY_MODES














