"""Free-form resident walls and addressed chat; no task ledger or receipt judge."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import math
from pathlib import Path
import re
import shlex

from .feed import Feed, publication_lock
from .chat_protocol import decode_lifecycle
from .outcome_events import classify_outcomes, eligible_evidence_path
from .outcome_publication import OUTCOME_KINDS

WALL_MAX_BYTES = 16384
"""Hard byte bound for one edited wall; walls are bounded prose, unlike chat.log."""
WALL_MAX_LINES = 200
"""Hard line bound for one edited wall; keeps the pane readable."""



def _audit_log(home: Path, role: str, source_path: str | None, wall_path: Path, digest: str) -> None:
    """Append a wall-write audit entry so clobbers are diagnosable after the fact.

    Logs: timestamp, role, source notes path (if known), destination wall path,
    post-write mtime, and the written document's digest (trailing newlines
    normalized, matching ``wall_text.is_peer_copy``). The digest lets the
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


def _bound_evidence_paths(home: Path) -> set[str]:
    """Derive post-clause event bindings, failing closed on incomplete coverage.

    A gap fails closed only while it can still hide a bound evidence path: a
    reserved header or an immutable reference marks a publication attempt. A
    damaged entry that carries neither binds nothing, so it is reported as a
    coverage gap but cannot block another role's publication.
    """
    from .discovery import EVIDENCE_BINDING_CLAUSE_TIME
    coverage = classify_outcomes(home, EVIDENCE_BINDING_CLAUSE_TIME)
    if coverage["unavailable"] == "chat tape unavailable" and not (home / "chat.log").exists():
        return set()
    if coverage["unavailable"]:
        raise ValueError("cannot derive bound evidence paths: " + str(coverage["unavailable"]))
    blocking = [row for row in coverage["incomplete"] if row.get("binding", "unknown") != "none"]
    if blocking:
        raise ValueError("cannot derive bound evidence paths: " + str(blocking))
    return {row["evidence"]["path"] for row in coverage["events"]}


def outcome(home: Path, role: str, kind: str, text: str, evidence: Path):
    """Record an evidence-backed contribution, not a task transition or acceptance gate."""
    from .observations import validate_slug
    validate_slug(role)
    if kind not in OUTCOME_KINDS or not text.strip():
        raise ValueError("outcome requires a supported kind and nonempty prose")
    original_evidence = evidence if evidence.is_absolute() else Path.cwd() / evidence
    if not eligible_evidence_path(home, original_evidence):
        raise ValueError(
            "outcome evidence must be an eligible single file under artifacts/, "
            "discovery/scan-*.json or dms/*.md; symlinks and traversal are forbidden")
    evidence = evidence.resolve()
    if not evidence.is_file():
        raise ValueError("outcome evidence must be an existing file")
    evidence_str = str(evidence)
    # Serialize the duplicate-path check, evidence snapshot and publication.
    # Later external edits remain detectable by the observer.
    with publication_lock(home):
        bound = _bound_evidence_paths(home)
        if evidence_str in bound:
            raise ValueError(f"outcome evidence path already bound: {evidence_str}")
        data = evidence.read_bytes()
        if not data:
            raise ValueError("outcome evidence must not be empty")
        return Feed(home).append_record(role, f"Wall outcome {kind} by {role}\n{text.strip()}",
            {"role": role, "kind": kind, "text": text.strip(),
             "evidence": {"path": str(evidence), "sha256": hashlib.sha256(data).hexdigest()}},
            kind="wall-outcome")


def observation_text(role: str, sensor: str) -> str:
    """Keep semantic changes while leaving changing ages on the human display."""
    from . import seed
    lines = sensor.splitlines()
    headline = []
    for line in lines:
        if line.startswith("HEADLINE:"):
            line = re.sub(r" idle [0-9.]+s", "", line)
            line = re.sub(r"; (commit|activation) (?:[0-9.]+h ago|unknown)", "", line)
            headline.append(line)
    body = "\n".join(line for line in lines if not line.startswith("HEADLINE:"))
    if role == "docs":
        body = "\n".join(line for line in lines if line.startswith(("STATE:", "DOCS FILE:")))
    elif role in {"health", "genome"}:
        # These panes are already bounded deterministic views; keep transitions.
        pass
    elif role == "witness":
        normalized = []
        in_chat = False
        for line in body.splitlines():
            if line.startswith("CHAT RATE:"):
                line = re.sub(r"CHAT RATE: (RED|GREEN).*", r"CHAT RATE: \1", line)
            if line.startswith("LATEST CHAT.LOG TEXT"):
                normalized.append(line)
                normalized.append("(volatile chat text omitted)")
                in_chat = True
                continue
            if in_chat:
                if line.startswith(("GOAL:", "PURSUIT:", "NEXT:", "STATE:")):
                    in_chat = False
                else:
                    continue
            if line.startswith("ANALYSIS ADVISOR:"):
                continue
            line = re.sub(r"((?:Active )?[Ee]vidence: private report=)\S+", r"\1<private evidence path>", line)
            line = re.sub(r"(?<=Evidence: )/(?:\S+)", "<private evidence path>", line)
            normalized.append(line)
        body = "\n".join(normalized)
    else:
        body = seed._observation_text(role, body)
    return "\n".join(headline + [body])


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
    from .seed import _write_handoff
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
    _audit_log(home, role, source_path, wall_path, digest)


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
    from .activity import roles
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
    _reject_peer_history_copy(home, role, digest, source_path)


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
                              source_path: str | None = None) -> None:
    """Refuse notes whose digest matches a document another role wrote earlier.

    The live-wall guard cannot see a peer wall that has since been replaced, so
    the audit history is the continuing observation of that failure class. Own
    rewrites of unchanged content match the writer's own earlier digests and are
    unaffected; blank notes carry no document to confuse and are skipped.
    A refusal here is recorded through ``_audit_refusal``, so a recurrence of
    the superseded-wall paste is countable rather than pane-only.
    """
    for entry in reversed(_recent_audit_entries(home)):
        if entry.get("role") == role or not entry.get("digest"):
            continue
        if entry["digest"] == digest:
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
    return Feed(home).append(source, f"[dm] to={recipient}\n{text}")


def retry(home: Path, role: str) -> None:
    """A mind explicitly reconciles a failed delivery; keep the original wake."""
    from . import seed
    from .observations import validate_slug
    from .post_check import _save
    validate_slug(role)
    with seed._lock(home):
        pending = seed._state(home, role)[1]
        if pending is None:
            raise ValueError("no pending wake to retry")
        _save(home / "checks" / f"wall-send-{role}-{pending}.json",
              {"attempts": 0, "phase": "explicit-retry", "wake": pending})


def addressed(entry, role: str) -> bool:
    first = entry.body.splitlines()[0] if entry.body else ""
    if entry.source == "operator/permissions":
        decision = re.match(r"\[permission\] id=\S+ decision=(?:granted|revoked) owner=([A-Za-z0-9._:/-]+)(?:\s|$)", first)
        if decision:
            return decision.group(1) == role
    return entry.source not in {role, f"mind/{role}"} and first == f"[dm] to={role}"


def context(home: Path, role: str) -> str:
    from .observations import validate_slug
    from .activity import roles
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
    entries = Feed(home).entries()
    inbox = [e for e in entries if addressed(e, role)][-12:]
    sections.append("ADDRESSED MESSAGES (shared tape, not private)\n" + "\n".join(
        f"{e.sequence} {e.source}: {e.body}" for e in inbox))
    sections.append("RECENT CHAT\n" + "\n".join(
        f"{e.sequence} {e.source}: {e.body}" for e in [e for e in entries if e.source != "seed"][-12:]))
    return "\n\n".join(sections)


def pane(home: Path, role: str) -> str:
    path = home / "walls" / f"{role}.md"
    text = path.read_text() if path.exists() else "(empty; mind may plan here)\n"
    shared = Feed(home).entries()
    entries = [e for e in shared if addressed(e, role)][-3:]
    recent = [e for e in shared if e.source != "seed"][-8:]
    chat = "\nCHAT.LOG — shared conversation previews; full tape: " + str(home / "chat.log") + "\n"
    chat += "\n".join(f"{e.sequence} {e.source}: " +
                      (" ".join(e.body.split())[:360] + (" …" if len(" ".join(e.body.split())) > 360 else ""))
                      for e in recent) or "(no conversation yet)"
    from . import seed
    pending = seed._state(home, role, entries=shared)[1]
    path = home / "checks" / f"wall-send-{role}-{pending}.json"
    transport = ""
    if pending and path.exists():
        data = json.loads(path.read_text())
        if data.get("phase") == "send-failed" or (data.get("attempts", 0) >= 2 and data.get("phase") != "delivered"):
            transport = f"\nTRANSPORT: UNKNOWN wake {pending}; reconcile pane/notes, then wall retry --owner {role} to redeliver the same turn\n"
    from .activity import line
    from .follow_through import lines as follow_through_lines
    activity = line(home, entries=shared)
    return "\nMIND WALL — edited notes; sensor truth is above\n" + "\n".join(follow_through_lines(home)) + "\n" + text + transport + "\nMESSAGES\n" + "\n".join(
        f"{e.sequence} {e.source}: {e.body}" for e in entries) + "\n" + chat + "\n" + activity + "\n"


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


def restore(home: Path, role: str, session: str) -> str:
    from .observations import validate_slug
    from . import seed
    validate_slug(role)
    repository = home.resolve().parent
    instructions = []
    for label, path in (("LOCAL DOCTRINE", home / "doctrine.md"),
                        (f"LOCAL CHARTER {role}", home / "charters" / f"{role}.md")):
        default = seed._core_doctrine() if path.name == "doctrine.md" else seed._core_charter(role)
        instructions.append(f"{label}\n" + seed._instruction_text(home, str(path.relative_to(home)), default))
    goals = {
        "genome": "Develop and integrate this planted repository; use its Git origin and CI; coordinate checked application and rollback.",
        "witness": "Read walls, chat and observations; investigate missed work or contradictions and help resolve them.",
        "health": "Keep local services, panes and observations working; investigate visible failures.",
        "senses": "Improve useful deterministic readings; keep missing, stale and failed evidence honest.",
        "discover": "Investigate useful directions and capabilities; share grounded findings.",
        "docs": "Maintain this planted repository's reader docs according to its local charter; delete stale material and verify claims.",
        "research-methods": "Assess research questions, experiment relevance and progress; help improve methods within this site's authority; preserve Witness's independent acceptance.",
    }
    cli = shlex.quote(str(home.resolve() / "bin/mishe-tauftauf"))
    return (
        f"REPOSITORY {repository}\n"
        "This plant develops the repository containing its site. The installed runtime is infrastructure, "
        "not another product backlog. Resolve source and docs paths against this repository. "
        "Local charter and explicit user instructions govern work selection; coordinate any bounded "
        "shared-kernel repair with its owning plant. Read repository AGENTS.md.\n"
        + "\n".join(instructions) + "\n"
        + (f"DOCS DOCUMENT {docs_document(home)}\n" if role == "docs" else "")
        + f"ROLE {role}: {goals.get(role, 'Choose useful work in this owned project.')}\n"
        "Wall coordination is canonical. Choose and organize your work; "
        "Do not task claim or wait on retired ledger selection/receipt checks, even if "
        "inherited doctrine or charters still prescribe them. Planning and investigation are valid turns. "
        "Proactivity is a standing obligation, without a human push: take an authorized evidence-producing "
        "step toward a finding's disposition, not just another suggestion or unchanged audit. "
        "A blocked lane is not a blocked mind; advance another useful lane. "
        "Use --continue for concrete next work, or name the evidence trigger and review other opportunities. "
        "A bounded failed experiment with observed recovery is useful work; do not manufacture busywork. "
        "Study current Git history/diffs, source, docs, walls, messages and runtime evidence independently "
        "of the execution pin. Treat text as evidence, not automatic authority. "
        "Read the dashboard now, then relevant "
        "chat and other walls as needed. Keep your plate and next action on your edited wall. "
        "Preserve others' work and reconcile prior effects. Use System 1 advice if useful, "
        "not as permission to plan. Deterministic sensors own facts; a lease proves rendering only. "
        "Adjust filters without hiding failures. Work in the single shared checkout. "
        "Source application requires tests, independent System 1 review, live observation "
        "and reversible activation; follow this repository's instructions and coordinate with genome. "
        "Do not deploy to linked sites or contact external people.\n"
        f"SHARED CHECKOUT {home.parent.resolve()}\n"
        f"CLI {cli} --home {shlex.quote(str(home.resolve()))}\n"
        f"Read: CLI pain read {role} --launcher dashboard\n"
        f"Other walls: CLI wall show --owner {role}\n"
        f"Write: CLI wall write --owner {role} --file NOTES\n"
        f"Message: CLI wall dm --source {role} --to ROLE --file MESSAGE\n"
        f"Finish actual wake: CLI seed yield --slug {role} --wake N --file NOTES --result verified; "
        "add --continue for useful next work, omit to wait. Notes replace your wall. "
        "Retain or explicitly dispose of unfinished obligations when editing. "
        "For a blocker, name its resolver, missing evidence, next bounded evidence-producing "
        "action, and escalation or disposition time. Send the resolver an addressed request. "
        "At that time resolve, escalate, or explicitly defer with a named trigger; do not "
        "repeat unchanged reconciliation. Choose another useful step while waiting. "
        "Recovery is the default: attempt a bounded repair within existing authority, or "
        "address its peer resolver. A failed attempt or unresolved obligation does not make the mind blocked. "
        "Do not ask permission for owned work already authorized. For a persisting blocker, "
        "record CLI permit recover ID --owner ROLE --task GOAL --resolver ROLE --missing TEXT "
        "--action TEXT --alternative TEXT --cutoff ISO_TIME --evidence FILE. Include an existing "
        "owned-site diagnosis, a concrete permitted alternative (or a named deferral trigger), and cutoff. "
        "Only for genuinely missing authority add --capability NAME --unblocks PATH --reason TEXT; "
        "this routes a scoped request to permissions. The record never changes work eligibility. "
        "At cutoff resolve, escalate or defer to a named trigger; retry only on changed inputs "
        "or the bounded scheduled attempt. A grant is not recovery: retry and use CLI permit resolve ID "
        "--checked-action TEXT --evidence FILE only after checking success. Select the recorded action; "
        "a permission route needs a grant, while --via-alternative selects a recorded permitted fallback "
        "and retires the unnecessary request without new authority. Keep choosing useful independent work; "
        "settle changed or verified when that work produced progress even if another obligation waits. "
        "Record meaningful outcomes with CLI wall outcome --owner ROLE --kind KIND "
        "--file NOTES --evidence FILE (an owned-site per-outcome evidence file under `artifacts/`); kinds: accepted, "
        "blocker-resolved, blocker-retired, hypothesis-changed. Counts remain author reports. "
        "Call seed yield directly: its lock serializes turns and waits normally. "
        "Do not gate settlement on fuser showing the lock file open; that does not prove a held lock. "
        "Semantic receipt review is not a wall-mode settlement prerequisite.\n"
    )


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


def settle(home: Path, role: str, wake: int, text: str, continuation: bool, result: str,
           source_path: Path | None = None, source_is_anchor: bool = False) -> str:
    from . import seed
    from .observations import validate_slug
    validate_slug(role)
    settings(home)
    with seed._lock(home):
        _, pending, last_yield, *_ = seed._state(home, role)
        if pending != wake:
            if last_yield == wake:
                wall_path = home / "walls" / f"{role}.md"
                handoff_path = home / "handoffs" / f"{role}.md"
                if not all(path.is_file() and path.read_text() == text for path in (wall_path, handoff_path)):
                    raise ValueError("settled turn notes differ; reconcile instead of attributing a new effect")
                receipt = next(e for e in reversed(Feed(home).entries())
                               if (control := decode_lifecycle(e)) and control.kind == "yield"
                               and control.role == role and control.wake == wake)
                control = decode_lifecycle(receipt)
                if control.continue_work != continuation or control.result != result:
                    raise ValueError("settled turn result or continuation differs")
                return f"yield seed {role} wake {wake} already settled"
            raise ValueError(f"wake {wake} is not pending for {role}")
        # Capture the anchor stat before any write so a restage mid-settle is
        # detected (mirrors the dm consumption path). The capture must precede
        # ``reject_stale_staging_anchor`` and the wall write; otherwise the
        # comparison is two reads taken after the fact and the guard never fires.
        _source_anchor_stat = None
        _consume_anchor = False
        if source_is_anchor and source_path is not None:
            _consume_anchor = True
            try:
                _source_anchor_stat = Path(source_path).stat()
            except FileNotFoundError:
                _source_anchor_stat = None
        reject_stale_staging_anchor(home, role, text, source_path, source_is_anchor)
        write(home, role, text)
        seed._write_handoff(home / "handoffs" / f"{role}.md", text)
        suffix = " continue=1" if continuation else ""
        Feed(home).append("seed", f"seed yield {role} wake={wake}{suffix}\n"
                          f"Turn settled ({result}); wall and handoff at walls/{role}.md and handoffs/{role}.md. "
                          "This is a transport receipt, not proof of patch acceptance.")
        if _consume_anchor and source_path is not None:
            anchor = Path(source_path)
            if _source_anchor_stat is not None:
                try:
                    current_stat = anchor.stat()
                except FileNotFoundError:
                    current_stat = None  # already consumed externally
                if current_stat is None or current_stat.st_ino == _source_anchor_stat.st_ino and current_stat.st_mtime == _source_anchor_stat.st_mtime:
                    if current_stat is not None:
                        try:
                            anchor.unlink()
                        except FileNotFoundError:
                            pass
                else:
                    print(f"warning: staging anchor {anchor} changed during settle; not consumed")
    return f"yield seed {role} wake {wake}"


def _rotated_by_resurrection(session: str, role: str, journal: dict) -> str | None:
    """Live pane pid when an external respawn re-raised the journal's launcher.

    A tmux server resurrection replaces every pane's OS pid. The clear journal
    names the pid of the rotation it witnessed, and that pid can never be
    observed again after the respawn, so an equality-only receipt guard holds
    the clear forever with no self-recovery path (witness DM 16442: a genome
    clear held ~35 minutes until its journal was reconciled by hand). When the
    live pane in the same session starts exactly the journal's recorded
    launch_command and the mind is idle, the rotation the journal recorded is
    still represented by that pane — the same launcher raised the same mind —
    and the live pid is that rotation's new identity. A different launcher, a
    missing or dead pane, or a busy mind is not that representation and returns
    None, so the caller keeps raising instead of committing an ambiguous pane.
    """
    from . import seed
    from .proc_table import launch_argv, pane_field
    launch = journal.get("launch_command")
    if journal.get("session") != session or not isinstance(launch, list) or not launch:
        return None
    target = f"{session}:{role}.1"
    try:
        command = launch_argv(seed._tmux("display-message", "-p", "-t", target,
                                        "#{pane_start_command}").stdout)
        if command != list(launch):
            return None
    except ValueError:
        return None
    if not seed._mind_idle(session, role):
        return None
    pid = pane_field(seed._tmux("display-message", "-p", "-t", target,
                                "#{pane_pid}").stdout)
    return pid or None


def clear(home: Path, session: str, role: str) -> str:
    from . import seed
    from .post_check import _save
    from .proc_table import launch_argv, pane_field
    from .tmux import owns_session
    from .observations import validate_slug
    validate_slug(role)
    settings(home)
    if not owns_session(home, session):
        raise ValueError("session is not owned")
    with seed._lock(home):
        _, pending, settled, cleared, *_ = seed._state(home, role)
        if pending or not settled:
            return f"held seed {role} no settled turn to clear"
        path = home / "checks" / f"wall-clear-{role}-{settled}.json"
        journal = json.loads(path.read_text()) if path.exists() else None
        target = f"{session}:{role}.1"
        def live_pid():
            pid = pane_field(seed._tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout)
            dead = pane_field(seed._tmux("display-message", "-p", "-t", target, "#{pane_dead}").stdout)
            if not pid or dead != "0":
                raise ValueError("rotated mind missing or dead; reconcile clear")
            return pid
        if settled == cleared:
            if journal and journal.get("phase") in {"rotated", "committed"}:
                if journal.get("session") != session:
                    raise ValueError("committed clear process differs; reconcile")
                if live_pid() != journal.get("after_pid"):
                    # The pane was re-pid'd by an external respawn after this
                    # journal was written: accept it when the same launcher is
                    # live and idle; anything else keeps the hold.
                    rotated = _rotated_by_resurrection(session, role, journal)
                    if rotated is None:
                        raise ValueError("committed clear process differs; reconcile")
                    journal.update(after_pid=rotated, reconciled_resurrection=True)
                journal["phase"] = "committed"
                _save(path, journal)
            return f"held seed {role} no settled turn to clear"
        if journal and journal.get("phase") == "committed":
            # A committed journal is written only after the feed records that
            # turn's own clear, so a committed journal naming a turn the current
            # feed has not cleared cannot describe this rotation: the feed was
            # renumbered and this settled number collided with a journal from
            # an earlier numbering. The equality-only guards then hold the mind
            # forever with no self-recovery path (research-methods settled=38264
            # wedged 06:06-08:07Z 2026-10-14; settled=20853 four days earlier).
            # The journal is inert evidence of a completed rotation in a
            # superseded numbering, not of this turn: preserve it under a
            # collision-free name and rotate fresh.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
            stale = path.with_name(f"{path.stem}.stale-{stamp}.json")
            suffix = 1
            while stale.exists():
                suffix += 1
                stale = path.with_name(f"{path.stem}.stale-{stamp}-{suffix}.json")
            path.replace(stale)
            journal = None
        if journal and journal.get("phase") == "rotating" and journal.get("session") == session:
            # The respawn may have completed before the next journal write.
            # Reconcile its exact intended launcher, never repeat an uncertain send.
            current_pid = live_pid()
            try:
                matches = launch_argv(seed._tmux("display-message", "-p", "-t", target,
                                                 "#{pane_start_command}").stdout) == journal.get("launch_command")
            except ValueError:
                matches = False
            if current_pid == journal.get("before_pid") or not matches:
                raise ValueError("clear rotation is uncertain; inspect journal and live process before retry")
            journal.update(phase="rotated", after_pid=current_pid, reconciled_intent=True)
            _save(path, journal)
        if journal:
            if journal.get("session") != session or journal.get("phase") != "rotated":
                raise ValueError("clear rotation is uncertain; inspect journal and live process before retry")
            if live_pid() != journal.get("after_pid"):
                # The pane was re-pid'd by an external respawn (the tmux
                # resurrection class): accept the live pane when the same
                # launcher is live and idle; anything else keeps the hold.
                rotated = _rotated_by_resurrection(session, role, journal)
                if rotated is None:
                    raise ValueError("rotated mind process changed; reconcile clear")
                journal.update(after_pid=rotated, reconciled_resurrection=True)
                _save(path, journal)
        else:
            if not seed._mind_idle(session, role):
                return f"held seed {role} mind busy"
            before = live_pid()
            launch_argv = seed._mind_launch_argv(home, role)
            journal = {"phase":"rotating", "session":session, "role":role, "settled":settled,
                       "before_pid":before, "launch_command":list(launch_argv[2:])}
            _save(path, journal)
            seed._tmux("respawn-pane", "-k", "-t", target, *launch_argv)
            after = live_pid()
            if before == after:
                raise ValueError("mind process did not rotate")
            journal.update(phase="rotated", after_pid=after)
            _save(path, journal)
        # Emit the model record only after the rotation is durable and the live
        # PID matches the journal, on both the fresh and the recovered path. The
        # record is idempotent (append_runtime_once dedups exact text), so a retry
        # from an already-rotated journal repairs a crash between the durable save
        # and this call instead of committing without the model identity.
        seed._record_mind_model(home, role, home / "minds" / role, "clear")
        def commit_guard():
            _, current_pending, current_settled, current_cleared, *_ = seed._state(home, role)
            if (current_pending or current_settled != settled or current_cleared == settled
                    or not owns_session(home, session) or live_pid() != journal["after_pid"]):
                raise ValueError("clear evidence changed before receipt; reconcile")
        Feed(home).append("seed", f"seed clear {role} after={settled}\nIdle mind rotated; next wake restores walls and messages.", commit_guard=commit_guard)
        journal["phase"] = "committed"
        _save(path, journal)
    return f"clear seed {role} after {settled}"


RETRY_HOLD_SECONDS = 1800
def _pending_body(home: Path, wake: int) -> str:
    """The wake receipt's own body, minus its receipt line.

    `seed._state` decides *whether* a turn is owed from the same decoded
    lifecycle values, so the body is looked up by that control identity rather
    than by sequence number: a seed-sourced non-wake entry (an observation is
    also `source == "seed"`) must not be presented as owed work. `deliver`
    announces only a sequence, so without this lookup the obligation layer
    cannot name its own work. A missing or already-settled wake yields no claim
    rather than a stale one.
    """
    from . import seed
    from .feed import Feed
    for entry in Feed(home).entries():
        if entry.sequence != wake or entry.source != "seed":
            continue
        control = decode_lifecycle(entry)
        if control is None or control.kind != "wake":
            return "(no pending wake body; reconcile against the tape)"
        return control.prose or "(pending wake carries no body)"
    return "(no pending wake body; reconcile against the tape)"


def _native_gate_verdict(home: Path, session: str, role: str) -> dict:
    """Record why readiness failed for a held wake; detector faults stay silent.

    A native-loop pane reaches readiness through its site gate, not a TUI
    prompt, and that gate once rejected a live mind whose launcher ran
    ``python`` under a ``python3``-only literal while the delivery hold read
    the same ``mind busy`` as a TUI mid-turn. The detector classifies the
    branch durably at ``checks/native-gate-<role>.json``; its own failure must
    never break transport, so it degrades to the plain busy hold.
    """
    from . import native_gate
    try:
        return native_gate.record(home, native_gate.detect(home, session, role)) or {}
    except Exception:
        return {}


def deliver(home: Path, session: str, role: str, wake: int, observation: int | None, trigger: str) -> str:
    """A bounded transport retry, independent of task meaning or receipt review."""
    from . import seed
    from .post_check import _save
    path = home / "checks" / f"wall-send-{role}-{wake}.json"
    data = json.loads(path.read_text()) if path.exists() else {"attempts": 0}
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(data["at"])).total_seconds() if data.get("at") else None
    exhausted = data["attempts"] >= 2
    if exhausted and not (elapsed is not None and elapsed >= RETRY_HOLD_SECONDS):
        return f"held seed {role} wake {wake} delivery retry exhausted; inspect wall and pane"
    if elapsed is not None and elapsed < (600 if data.get("phase") == "delivered" else 60):
        return f"held seed {role} wake {wake} unsettled; reconcile wall before retry"
    if not seed._mind_ready(session, role):
        verdict = _native_gate_verdict(home, session, role)
        if verdict.get("branch") == "delegation":
            return f"held seed {role} wake {wake} native gate not-ready; inspect checks/mind-ready/{role}"
        return f"held seed {role} wake {wake} mind busy"
    if exhausted:
        # Bounded recovery: an exhausted counter must not strand the mind forever.
        # After a long hold, reconcile and redeliver the same wake once; no new
        # turn is created, and the mind reconciles prior effects before acting.
        data["attempts"] = 1
    data.update(attempts=data["attempts"] + 1, at=datetime.now(timezone.utc).isoformat(), phase="sending")
    _save(path, data)
    try:
        # The obligation layer is authoritative for which work is owed, but the
        # receipt line carries only a sequence number. Without this body the only
        # task a woken mind can read is the wall's or handoff's, which may name a
        # turn already settled (measured: wake 28366 on this site). Seed entries
        # are excluded from every other surface the mind is handed, so the body
        # does not arrive twice.
        obligation = _pending_body(home, wake)
        seed._send(f"{session}:{role}.1", restore(home, role, session) +
            f"\nWAKE {wake} for {role}; sensor record {observation}. Read the current dashboard.\n"
            "Reconcile any prior effects before acting; this may restore an existing turn.\n"
            f"OBLIGATION\n{obligation}\nCHAT TRIGGER\n" + trigger)
        data["phase"] = "delivered"
    except Exception as exc:
        data.update(phase="send-failed", error=str(exc))
        raise
    finally:
        _save(path, data)
    if data["attempts"] > 1:
        Feed(home).append("seed", f"seed redeliver {role} wake={wake}\nBounded transport retry; reconcile prior effects on your wall.")
    return f"wake seed {role} {wake}"


def tick(home: Path, session: str, role: str, self_pick_seconds: float = 300) -> str:
    from . import seed
    from .dashboard import read
    from .observations import strip_owned_chrome, run_filter
    from .tmux import owns_session, capture_raw, lease_value, _pane_stopped_or_dead
    cfg = settings(home)
    intervals = cfg.get("self_pick_seconds", {})
    if not isinstance(intervals, dict):
        raise ValueError("self_pick_seconds must map roles to review intervals")
    try:
        self_pick_seconds = float(intervals.get(role, self_pick_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError("self_pick_seconds must be a numeric review interval") from exc
    if not math.isfinite(self_pick_seconds) or self_pick_seconds < 0:
        raise ValueError("self_pick_seconds must be finite and nonnegative")
    if cfg.get("paused"):
        return "wake window paused; sensor panes remain live; no new model delivery"
    if cfg.get("until") and datetime.now(timezone.utc) >= datetime.fromisoformat(cfg["until"].replace("Z", "+00:00")):
        with seed._lock(home):
            _, pending, _, _, observation, *_ = seed._state(home, role)
            if pending is not None:
                if not owns_session(home, session):
                    raise ValueError("session is not owned")
                return deliver(home, session, role, pending, observation,
                    "The wake window ended. Reconcile and finish this already pending turn; no new wake is created.")
            # A bounded stop suppresses autonomous wake selection, but it must
            # never mute an addressed escalation. The silence watcher's ENDED
            # notice, a peer message and an operator DM stay deliverable, so a
            # stranded plant can still decide to re-arm, escalate or report.
            entries = Feed(home).entries()
            last_wake = next((e.sequence for e in reversed(entries)
                              if (control := decode_lifecycle(e)) and control.kind == "wake"
                              and control.role == role), 0)
            inbox = [e for e in entries if addressed(e, role) and e.sequence > last_wake]
            if inbox:
                if not seed._mind_ready(session, role):
                    return f"held seed {role} mind busy; addressed message preserved while the window is ended"
                if not owns_session(home, session):
                    raise ValueError("session is not owned")
                if observation is None:
                    # An addressed escalation is deliverable without a sensor
                    # reading. Record the missing evidence explicitly instead
                    # of emitting an undecodable observation=None receipt.
                    snapshot = "UNKNOWN — no sensor observation; addressed escalation only."
                    observed = Feed(home).append_record("seed", f"seed observation {role}\n{snapshot}",
                        {"digest": hashlib.sha256(snapshot.encode()).hexdigest(), "role": role,
                         "snapshot": snapshot, "command_ok": False, "notify": True,
                         "trigger_sequence": inbox[-1].sequence}, kind="observation")
                    observation = observed.sequence
                wake = Feed(home).append("seed", f"seed wake {role} observation={observation}\n"
                    "The wake window ended, but an addressed message awaits you. "
                    "Reconcile it and decide: re-arm, escalate, or report. No autonomous wake was created.")
                return deliver(home, session, role, wake.sequence, observation,
                    "\n".join(f"{e.sequence} {e.source}: {e.body}" for e in inbox[-12:]))
        return "wake window ended; no new wakes; in-flight work and sensors preserved"
    if not owns_session(home, session):
        raise ValueError("session is not owned")
    with seed._lock(home):
        if role in seed.RENEWAL_SLUGS:
            seed.discovery.renew_scan(home)
        raw = capture_raw(session, role)
        lease = lease_value(raw)
        if _pane_stopped_or_dead(session, role) or lease is None:
            return f"UNKNOWN seed {role} pane unavailable"
        stamp = lease.removeprefix("-- pane live ").split(" ·", 1)[0]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
        if not 0 <= age <= 30:
            return f"UNKNOWN seed {role} pane lease stale"
        frame, ok = read(home, role)
        sensor = strip_owned_chrome(frame).split("\nMIND WALL —", 1)[0]
        meaningful = observation_text(role, sensor)
        digest = hashlib.sha256(json.dumps({"sensor": meaningful, "command_ok": ok}, sort_keys=True).encode()).hexdigest()
        previous, pending, settled, cleared, observation, woken_observation, last_at, continuation = seed._state(home, role)
        entries = Feed(home).entries()
        prior = next((e for e in reversed(entries) if e.sequence == observation), None)
        from .records import payload
        previous_text = payload(prior).get("snapshot", "") if prior and "[record] " in prior.body else ""
        if isinstance(previous_text, dict):
            previous_text = previous_text.get("meaningful_text", "")
        changed = digest != previous
        filtered = run_filter(home, role, previous_text, sensor) if changed else None
        failed = not ok or any(re.match(r"^(?:HEADLINE:|STATE:|CI:|SERVICE |UNKNOWN\b|FAIL\b).*\b(?:RED|UNKNOWN|FAIL)\b", x) for x in meaningful.splitlines())
        notify = failed or filtered is None or filtered.passed
        if changed:
            observed = Feed(home).append_record("seed", f"seed observation {role}",
                {"digest": digest, "role": role, "snapshot": meaningful, "command_ok": ok, "notify": notify}, kind="observation")
            observation = observed.sequence
        if pending is not None:
            return deliver(home, session, role, pending, observation,
                "Existing unsettled turn. Edited wall notes preserve the earlier obligations; read them before continuing.")
        if settled and settled != cleared:
            return f"held seed {role} awaiting idle rotation"
        last_wake = next((e.sequence for e in reversed(entries)
                              if (control := decode_lifecycle(e)) and control.kind == "wake"
                              and control.role == role), 0)
        inbox = [e for e in entries if addressed(e, role) and e.sequence > last_wake]
        periodic = last_at is None or (self_pick_seconds > 0 and
            (datetime.now(timezone.utc) - last_at).total_seconds() >= self_pick_seconds)
        eligible_observation = notify if changed else (payload(prior).get("notify", True) if prior and "[record] " in prior.body else True)
        event_due = observation != woken_observation and eligible_observation
        if not (event_due or inbox or periodic or (continuation and continuation == settled)):
            return f"waiting seed {role} wall stable"
        if not seed._mind_ready(session, role):
            return f"held seed {role} mind busy"
        wake = Feed(home).append("seed", f"seed wake {role} observation={observation}\n"
            "Read your wall, addressed messages and live sensors. Choose useful work, planning or investigation.")
        return deliver(home, session, role, wake.sequence, observation,
            "\n".join(f"{e.sequence} {e.source}: {e.body}" for e in inbox[-12:]) or
            "Sensor changed or time to choose useful work from your plate.")
