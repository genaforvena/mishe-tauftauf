"""Free-form resident walls and addressed chat; no task ledger or receipt judge."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shlex

from .feed import Feed

OUTCOME_KINDS = ("accepted", "blocker-resolved", "blocker-retired", "hypothesis-changed")
WALL_MAX_BYTES = 16384
"""Hard byte bound for one edited wall; walls are bounded prose, unlike chat.log."""
WALL_MAX_LINES = 200
"""Hard line bound for one edited wall; keeps the pane readable."""


def outcome(home: Path, role: str, kind: str, text: str, evidence: Path):
    """Record an evidence-backed contribution, not a task transition or acceptance gate."""
    from .observations import validate_slug
    validate_slug(role)
    if kind not in OUTCOME_KINDS or not text.strip():
        raise ValueError("outcome requires a supported kind and nonempty prose")
    evidence = evidence.resolve()
    if not evidence.is_relative_to(home.resolve()) or not evidence.is_file():
        raise ValueError("outcome evidence must be an existing file inside the owned site")
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
    elif role in {"health", "witness", "genome"}:
        # These panes are already bounded deterministic views. Legacy role filters
        # discard PATCH/SERVICES and must not hide their state transitions here.
        pass
    else:
        body = seed._observation_text(role, body)
    return "\n".join(headline + [body])


def settings(home: Path) -> dict:
    path = home / "coordination-mode.json"
    if not path.exists():
        return {"mode": "ledger"}
    try:
        data = json.loads(path.read_text())
        if data.get("mode") not in {"wall", "ledger"}:
            raise ValueError("invalid mode")
        if data.get("until"):
            deadline = datetime.fromisoformat(data["until"])
            if deadline.tzinfo is None:
                raise ValueError("deadline needs timezone")
        return data
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"coordination mode unavailable: {exc}") from exc


def enabled(home: Path) -> bool:
    return settings(home)["mode"] == "wall"


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


def write(home: Path, role: str, text: str) -> None:
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
    _write_handoff(home / "walls" / f"{role}.md", text)


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
    validate_slug(role)
    sections = []
    own = home / "walls" / f"{role}.md"
    sections.append(f"YOUR WALL ({own})\n" + (own.read_text() if own.exists() else "(empty; write your plan here)"))
    for path in sorted((home / "walls").glob("*.md")):
        if path != own:
            sections.append(f"WALL {path.stem}\n{path.read_text()}")
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
        if data.get("phase") == "send-failed" or data.get("attempts", 0) >= 2:
            transport = f"\nTRANSPORT: UNKNOWN wake {pending}; reconcile pane/notes, then wall retry --owner {role} to redeliver the same turn\n"
    from .activity import line
    activity = line(home, entries=shared)
    return "\nMIND WALL — edited notes; sensor truth is above\n" + text + transport + "\nMESSAGES\n" + "\n".join(
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
    validate_slug(role)
    repository = home.resolve().parent
    instructions = []
    for label, path in (("LOCAL DOCTRINE", home / "doctrine.md"),
                        (f"LOCAL CHARTER {role}", home / "charters" / f"{role}.md")):
        instructions.append(f"{label}\n" + (path.read_text() if path.exists() else "(not supplied; read repository AGENTS.md)"))
    goals = {
        "genome": "Develop and integrate this planted repository; use its Git origin and CI; coordinate checked application and rollback.",
        "witness": "Read walls, chat and observations; investigate missed work or contradictions and help resolve them.",
        "health": "Keep local services, panes and observations working; investigate visible failures.",
        "senses": "Improve useful deterministic readings; keep missing, stale and failed evidence honest.",
        "discover": "Investigate useful directions and capabilities; share grounded findings.",
        "docs": "Maintain this planted repository's reader docs according to its local charter; delete stale material and verify claims.",
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
        "Wall trial supersedes ledger and branch instructions. Choose and organize your work; "
        "Do not task claim or wait on ledger selection/receipt checks in wall mode, even if "
        "inherited doctrine or charters still prescribe them. They are not permission gates for this trial. "
        "planning and investigation are valid turns. Read the dashboard now, then relevant "
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
        "--file NOTES --evidence FILE (an owned-site evidence file); kinds: accepted, "
        "blocker-resolved, blocker-retired, hypothesis-changed. Counts remain author reports. "
        "Call seed yield directly: its lock serializes turns and waits normally. "
        "Do not gate settlement on fuser showing the lock file open; that does not prove a held lock. "
        "Semantic receipt review is not a wall-mode settlement prerequisite.\n"
    )


def settle(home: Path, role: str, wake: int, text: str, continuation: bool, result: str) -> str:
    from . import seed
    with seed._lock(home):
        _, pending, last_yield, *_ = seed._state(home, role)
        if pending != wake:
            if last_yield == wake:
                return f"yield seed {role} wake {wake} already settled"
            raise ValueError(f"wake {wake} is not pending for {role}")
        write(home, role, text)
        seed._write_handoff(home / "handoffs" / f"{role}.md", text)
        suffix = " continue=1" if continuation else ""
        Feed(home).append("seed", f"seed yield {role} wake={wake}{suffix}\n"
                          f"Turn settled ({result}); wall and handoff at walls/{role}.md and handoffs/{role}.md. "
                          "This is a transport receipt, not proof of patch acceptance.")
    return f"yield seed {role} wake {wake}"


def clear(home: Path, session: str, role: str) -> str:
    from . import seed
    from .tmux import owns_session
    if not owns_session(home, session):
        raise ValueError("session is not owned")
    with seed._lock(home):
        _, pending, settled, cleared, *_ = seed._state(home, role)
        if pending or not settled or settled == cleared:
            return f"held seed {role} no settled turn to clear"
        if not seed._mind_idle(session, role):
            return f"held seed {role} mind busy"
        target = f"{session}:{role}.1"
        before = seed._tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout
        seed._tmux("respawn-pane", "-k", "-t", target, *seed._mind_launch_argv(home, role))
        after = seed._tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout
        if before == after:
            raise ValueError("mind process did not rotate")
        Feed(home).append("seed", f"seed clear {role} after={settled}\nIdle mind rotated; next wake restores walls and messages.")
    return f"clear seed {role} after {settled}"


def deliver(home: Path, session: str, role: str, wake: int, observation: int | None, trigger: str) -> str:
    """A bounded transport retry, independent of task meaning or receipt review."""
    from . import seed
    from .post_check import _save
    path = home / "checks" / f"wall-send-{role}-{wake}.json"
    data = json.loads(path.read_text()) if path.exists() else {"attempts": 0}
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(data["at"])).total_seconds() if data.get("at") else None
    if data["attempts"] >= 2:
        return f"held seed {role} wake {wake} delivery retry exhausted; inspect wall and pane"
    if elapsed is not None and elapsed < (600 if data.get("phase") == "delivered" else 60):
        return f"held seed {role} wake {wake} unsettled; reconcile wall before retry"
    if not seed._mind_ready(session, role):
        return f"held seed {role} wake {wake} mind busy"
    data.update(attempts=data["attempts"] + 1, at=datetime.now(timezone.utc).isoformat(), phase="sending")
    _save(path, data)
    try:
        seed._send(f"{session}:{role}.1", restore(home, role, session) +
            f"\nWAKE {wake} for {role}; sensor record {observation}. Read the current dashboard.\n"
            "Reconcile any prior effects before acting; this may restore an existing turn.\nCHAT TRIGGER\n" + trigger)
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
        return "wall trial paused; sensor panes remain live; no new model delivery"
    if cfg.get("until") and datetime.now(timezone.utc) >= datetime.fromisoformat(cfg["until"]):
        with seed._lock(home):
            _, pending, _, _, observation, *_ = seed._state(home, role)
            if pending is not None:
                if not owns_session(home, session):
                    raise ValueError("session is not owned")
                return deliver(home, session, role, pending, observation,
                    "The trial window ended. Reconcile and finish this already pending turn; no new wake is created.")
            # A bounded stop suppresses autonomous wake selection, but it must
            # never mute an addressed escalation. The silence watcher's ENDED
            # notice, a peer message and an operator DM stay deliverable, so a
            # stranded plant can still decide to re-arm, escalate or report.
            entries = Feed(home).entries()
            last_wake = next((e.sequence for e in reversed(entries) if e.body.startswith(f"seed wake {role} ")), 0)
            inbox = [e for e in entries if addressed(e, role) and e.sequence > last_wake]
            if inbox:
                if not seed._mind_ready(session, role):
                    return f"held seed {role} mind busy; addressed message preserved while the window is ended"
                if not owns_session(home, session):
                    raise ValueError("session is not owned")
                wake = Feed(home).append("seed", f"seed wake {role} observation={observation}\n"
                    "The trial window ended, but an addressed message awaits you. "
                    "Reconcile it and decide: re-arm, escalate, or report. No autonomous wake was created.")
                return deliver(home, session, role, wake.sequence, observation,
                    "\n".join(f"{e.sequence} {e.source}: {e.body}" for e in inbox[-12:]))
        return "wall trial ended; no new wakes; in-flight work and sensors preserved"
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
            observed = Feed(home).append_record("seed", f"seed observation {role}\n"
                f"Fresh sensor snapshot saved; renderer command {'passed' if ok else 'failed'}. "
                "The mind decides what this means for its work.",
                {"digest": digest, "role": role, "snapshot": meaningful, "command_ok": ok, "notify": notify}, kind="observation")
            observation = observed.sequence
        if pending is not None:
            return deliver(home, session, role, pending, observation,
                "Existing unsettled turn. Trial wall notes preserve the earlier obligations; read them before continuing.")
        if settled and settled != cleared:
            return f"held seed {role} awaiting idle rotation"
        last_wake = next((e.sequence for e in reversed(entries) if e.body.startswith(f"seed wake {role} ")), 0)
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
