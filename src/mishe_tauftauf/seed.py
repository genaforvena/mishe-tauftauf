"""Small tmux and text loop for a resident, self-tending channel."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, publication_lock
from .records import payload as record_payload
from . import discovery, task_state
from .observations import executable, strip_owned_chrome, validate_home, validate_slug
from .tmux import OWNED_OPTION, _pane_stopped_or_dead, _python_command, _tmux, capture_raw, lease_value, owns_session


RENEWAL_SLUGS = frozenset({"discover", "senses"})
"""Resident channels whose panes depend on discovery scan freshness."""


OBS_RE = re.compile(r"seed observation ([a-z0-9-]+)(?: sha256=([0-9a-f]{64}))?\Z")
WAKE_RE = re.compile(r"seed wake ([a-z0-9-]+) observation=([1-9][0-9]*)(?: event=([1-9][0-9]*))?(?: task=(\S+))?\Z")
YIELD_RE = re.compile(r"seed yield ([a-z0-9-]+) wake=([1-9][0-9]*)( continue=1)?\Z")
CLEAR_RE = re.compile(r"seed clear ([a-z0-9-]+) after=([1-9][0-9]*)\Z")
# OMP's idle status can include a token after INSERT, e.g. "INSERT y >".
# Recognize that status without treating a spinner-bearing line as idle.
_OMP_INSERT_PROMPT_RE = re.compile(r"\s*π > INSERT(?: +[A-Za-z0-9!?-]+)* >")
_OMP_NORMAL_PROMPT_RE = re.compile(r"\s*π > NORMAL >")


def _omp_idle_mode(pane: str) -> str | None:
    mode = None
    for line in pane.splitlines():
        status = line.lstrip()
        if status.startswith(tuple("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")) and ("Working..." in status or "> INSERT " in status or "> NORMAL " in status):
            return None
        if _OMP_INSERT_PROMPT_RE.match(line):
            mode = "insert"
        elif _OMP_NORMAL_PROMPT_RE.match(line):
            mode = "normal"
    return mode
# The pre-baseline plant copied this exact doctrine into each site. Recognize it
# during the first upgrade so an untouched copy is not replayed as local policy.
LEGACY_INSTRUCTION_HASHES = {
    "doctrine.md": {"f95fd1012c9056047c55d4cec5ab2c1c7b2d2f3833f573f4bce22d689652a0be"},
}


def recorded_session(home: Path) -> str | None:
    """The session this site raised, from its receipt, or None when unraised."""
    try:
        return (home / ".seed-raised").read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        return None

@contextmanager
def _lock(home: Path):
    validate_home(home)
    with (home / ".seed.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _require_worktree(home: Path) -> None:
    """Gate the only legitimate site-creation path on the same rule plant() applies.

    init() seeds a brand new site, so it cannot reuse validate_home(); instead it must
    refuse the stray-generating shapes outright: a site is planted directly inside a Git
    worktree, never as a plant-home-shaped subtree of another directory. Tests plant a
    site by calling init() inside a freshly initialized repository, which this allows.
    """
    if (home.resolve() / ".git").exists():
        raise ValueError(f"site cannot be a repository root: {home}")
    workspace = home.parent.resolve()
    if not workspace.is_dir():
        raise ValueError(f"site must be directly inside a Git worktree: {workspace}")
    repository = subprocess.run(["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True)
    if repository.returncode or Path(repository.stdout.strip()).resolve() != workspace:
        raise ValueError(f"site must be directly inside a Git worktree: {workspace}")


def _receipt_line(body: str) -> str:
    """Keep replay compatible with old one-line and new explained receipts."""
    return body.splitlines()[0]


def _state(home: Path, slug: str, *, entries=None) -> tuple[str | None, int | None, int | None, int | None, int | None, int | None, datetime | None, int | None]:
    if entries is None:
        entries = Feed(home).entries()
    digest = None
    last_observation = None
    last_woken_observation = None
    last_wake_at = None
    pending = None
    last_yield = None
    last_clear = None
    continue_yield = None
    for entry in entries:
        if entry.source != "seed":
            continue
        line = _receipt_line(entry.body)
        if match := OBS_RE.fullmatch(line):
            if match.group(1) == slug:
                digest = match.group(2) or record_payload(entry)["digest"]
                last_observation = entry.sequence
        elif match := WAKE_RE.fullmatch(line):
            if match.group(1) == slug:
                pending = entry.sequence
                last_woken_observation = int(match.group(2))
                last_wake_at = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        elif match := YIELD_RE.fullmatch(line):
            if match.group(1) == slug and pending == int(match.group(2)):
                last_yield = pending
                continue_yield = pending if match.group(3) else None
                pending = None
        elif match := CLEAR_RE.fullmatch(line):
            if match.group(1) == slug:
                last_clear = int(match.group(2))
    return digest, pending, last_yield, last_clear, last_observation, last_woken_observation, last_wake_at, continue_yield


def _external_event(home: Path, slug: str):
    latest = None
    last_woken = None
    entries = Feed(home).entries()
    tasks = task_state.states(entries)
    for entry in entries:
        if entry.source == "seed" and (match := WAKE_RE.fullmatch(_receipt_line(entry.body))) and match.group(1) == slug:
            if match.group(3):
                last_woken = int(match.group(3))
        elif entry.source not in {slug, f"mind/{slug}"}:
            body = entry.body.lstrip(" \t")
            task = re.match(r"\[task\]\s+\S+\s+owner=([a-z0-9-]+)(?:\s|$)", body)
            decision = re.match(r"\[permission\]\s+id=\S+\s+decision=(?:granted|revoked)\s+owner=([a-z0-9-]+)(?:\s|$)", body)
            task_id = body.split()[1] if task is not None else None
            task_ready = (task_id in tasks and tasks[task_id].owner == slug and
                          task_state.eligible(tasks[task_id], entries))
            if (task is not None and task.group(1) == slug and task_ready) or (slug == "witness" and
                    (body.startswith("[wish]") or
                     (entry.source in {"operator", "operator/permissions"} and not body.startswith("[task]")))) or (
                         decision is not None and decision.group(1) == slug):
                latest = entry
    return latest if latest is not None and latest.sequence > (last_woken or 0) else None


def clear_stalls(entries, *, now: datetime | None = None, timeout: float = 120):
    """Find settled wakes whose idle clear has not happened within its budget."""
    now = now or datetime.now(timezone.utc)
    pending = {}
    settled = {}
    for entry in entries:
        if entry.source != "seed":
            continue
        line = _receipt_line(entry.body)
        if match := WAKE_RE.fullmatch(line):
            pending[match[1]] = entry.sequence
            settled.pop(match[1], None)
        elif match := YIELD_RE.fullmatch(line):
            if pending.get(match[1]) == int(match[2]):
                pending.pop(match[1])
                settled[match[1]] = entry
        elif match := CLEAR_RE.fullmatch(line):
            receipt = settled.get(match[1])
            if receipt and int(YIELD_RE.fullmatch(_receipt_line(receipt.body))[2]) == int(match[2]):
                settled.pop(match[1])
    return {slug: entry for slug, entry in settled.items()
            if (now - datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))).total_seconds() >= timeout}



def _observation_text(slug: str, frame: str) -> str:
    if slug == "witness":
        # Its pane includes the latest chat text and rate counts. Hashing that
        # text makes witness observe its own observation receipt forever.
        prefixes = ("WINDOWS:", "CI:", "OPEN TASKS:", "LOOP:", "CLEAR STALL:", "STATE:",
                    "COORDINATION:", "ANOMALY:", "PUBLICATION GATE:", "PUBLICATION RESULT:")
        lines = []
        in_recent_chat = False
        final_state = None
        for line in frame.splitlines():
            if line.startswith("LATEST CHAT.LOG TEXT"):
                in_recent_chat = True
                continue
            if in_recent_chat:
                if line.startswith("STATE:"):
                    final_state = line
                continue
            if line.startswith("CHAT RATE:"):
                lines.append(line.split(" —", 1)[0])
            elif line.startswith(prefixes):
                lines.append(line)
            else:
                task = re.fullmatch(r"(\S+ owner=[a-z0-9-]+ state=(?:open|taking)) at=\d+", line)
                if task is not None:
                    lines.append(task.group(1))
        if final_state is not None:
            lines.append(final_state)
        return "\n".join(lines)
    if slug == "health":
        # Sample values stay readable on the pane. Only check states should
        # wake health; byte and inode counts change on nearly every refresh.
        prefixes = ("STATE:", "PLANT STATE:", "HOST HEALTH:", "COVERAGE:",
                    "DOCTOR:", "WINDOWS:", "SERVICE ", "CI:",
                    "PASS ", "FAIL ", "UNKNOWN ")
        return "\n".join(line for line in frame.splitlines() if line.startswith(prefixes))
    if slug not in {"discover", "senses"}:
        return frame
    prefixes = ("STATE:", "UNKNOWN ", "UNAVAILABLE command.", "AVAILABLE command.",
                "PERMISSION REQUESTS:", "REQUEST ") if slug == "discover" else ("STATE:", "UNKNOWN ")
    return "\n".join(line for line in frame.splitlines() if line.startswith(prefixes))


def _send(target: str, message: str) -> None:
    # Agent TUIs detect paste bursts. Submit a complete bracketed paste after
    # its terminator, as the mesh's live pane delivery does.
    engine = _tmux("display-message", "-p", "-t", target, "#{pane_current_command}", check=False)
    if engine.returncode == 0 and engine.stdout.decode().strip() == "omp":
        pane = _tmux("capture-pane", "-p", "-t", target, check=False)
        mode = _omp_idle_mode(pane.stdout.decode("utf-8", "replace")) if pane.returncode == 0 else None
        if mode is None:
            raise ValueError("OMP idle input mode unavailable; delivery held")
        if mode == "normal":
            # OMP's normal editor mode is idle but interprets letters as Vim
            # commands. Enter insertion before /clear or a wake's paste.
            _tmux("send-keys", "-t", target, "i")
            for _ in range(10):
                time.sleep(0.1)
                inserted = _tmux("capture-pane", "-p", "-t", target, check=False)
                if inserted.returncode == 0 and _omp_idle_mode(inserted.stdout.decode("utf-8", "replace")) == "insert":
                    break
            else:
                raise ValueError("OMP did not enter insertion mode; delivery held")
    _tmux("send-keys", "-t", target, "C-u")
    if message == "/clear":
        _tmux("send-keys", "-t", target, "-l", message)
    else:
        buffer = f"mishe-seed-{os.getpid()}-{time.time_ns()}"
        session = target.split(":", 1)[0]
        owned = _tmux("show-option", "-qv", "-t", session, OWNED_OPTION)
        directory = Path(owned.stdout.decode().strip()) / "tmp"
        directory.mkdir(parents=True, exist_ok=True)
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix="wake-", delete=False) as handle:
                path = Path(handle.name)
                handle.write(message)
            # tmux set-buffer accepts the whole message as one command argument
            # and rejects a live wake once charter and handoff grow large.
            _tmux("load-buffer", "-b", buffer, str(path))
            _tmux("paste-buffer", "-p", "-d", "-b", buffer, "-t", target)
        finally:
            try:
                _tmux("delete-buffer", "-b", buffer, check=False)
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)
        time.sleep(0.5)
    _tmux("send-keys", "-t", target, "C-m")
    if message != "/clear":
        engine = _tmux("display-message", "-p", "-t", target, "#{pane_current_command}", check=False)
        if engine.returncode == 0 and engine.stdout.decode().strip() == "omp":
            # OMP first turns a long bracketed paste into an attachment card.
            # The first Enter can finish that conversion without submitting it.
            # Only send another Enter while the card still sits at an idle prompt.
            time.sleep(1.0)
            pane = _tmux("capture-pane", "-p", "-t", target, check=False)
            if pane.returncode == 0:
                lines = pane.stdout.decode("utf-8", "replace").splitlines()
                prompt = next((index for index in range(len(lines) - 1, -1, -1)
                               if _OMP_INSERT_PROMPT_RE.match(lines[index])), None)
                before_prompt = ([line for line in lines[max(0, prompt - 12):prompt] if line.strip()]
                                 if prompt is not None else [])
                card_open = any(line.startswith("╭── 📄 #") for line in before_prompt)
                card_ends_at_prompt = bool(before_prompt and before_prompt[-1].startswith("╰") and
                                           before_prompt[-1].endswith("╯"))
                if card_open and card_ends_at_prompt and _mind_ready(target.split(":", 1)[0],
                                                                     target.split(":", 1)[1].split(".", 1)[0]):
                    # Attachment-only Enter can leave OMP idle indefinitely.
                    # A short real input line submits the existing card, without
                    # pasting another wake or touching a busy model's input.
                    _tmux("send-keys", "-t", target, "-l",
                          "Read the attached instructions; reconcile prior effects and the current dashboard.")
                    _tmux("send-keys", "-t", target, "C-m")


def _mind_ready(session: str, slug: str) -> bool:
    """Deliver only at a known idle prompt or a site's explicit readiness gate."""
    target = f"{session}:{slug}.1"
    command = _tmux("display-message", "-p", "-t", target, "#{pane_current_command}", check=False)
    if command.returncode:
        return False
    engine = command.stdout.decode().strip()
    if engine not in {"omp", "codex"}:
        owned = _tmux("show-option", "-qv", "-t", session, OWNED_OPTION, check=False)
        if owned.returncode:
            return False
        probe = Path(owned.stdout.decode().strip()) / "checks" / "mind-ready" / slug
        if not executable(probe):
            return False
        env = os.environ.copy()
        env.update(MISHE_SEED_SESSION=session, MISHE_SEED_ROLE=slug, MISHE_SEED_PANE=target)
        try:
            return subprocess.run([str(probe)], env=env, capture_output=True, timeout=2).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    pane = _tmux("capture-pane", "-p", "-t", target, check=False)
    if pane.returncode:
        return False
    lines = pane.stdout.decode("utf-8", "replace").splitlines()
    if engine == "codex":
        if any(line.lstrip().startswith(("• Working", "• Waiting for background terminal", "• Running",
                                        "• Thinking", "• Executing")) for line in lines):
            return False
        return any(line.lstrip().startswith("› ") for line in lines)
    return _omp_idle_mode("\n".join(lines)) is not None


def _redeliver_pending(home: Path, session: str, slug: str, pending: int) -> str:
    """Retry an unsettled wake only after a quiet, visibly idle interval."""
    last_delivery = None
    last_delivery_sequence = None
    for entry in Feed(home).entries():
        if entry.body.startswith(f"seed wake {slug} ") and entry.sequence == pending:
            last_delivery = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
            last_delivery_sequence = entry.sequence
        elif _receipt_line(entry.body) == f"seed redeliver {slug} wake={pending}":
            last_delivery = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
            last_delivery_sequence = entry.sequence
    if last_delivery is None:
        return f"UNKNOWN seed {slug} wake {pending} has no delivery record"
    if (datetime.now(timezone.utc) - last_delivery).total_seconds() < 60:
        return f"held seed {slug} wake {pending} unsettled"
    from .post_check import require, _save
    from .coordination_checks import episode
    journal_path = home / "checks" / f"redeliver-{slug}-{pending}-after-{last_delivery_sequence}.json"
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else None
    if journal and journal.get("phase") == "intent":
        raise ValueError(f"redelivery send is uncertain; reconcile {journal_path} before another notification")
    if journal and journal.get("phase") not in {"completed", "committed"}:
        raise ValueError(f"redelivery journal phase unavailable; reconcile {journal_path}")
    if not journal and not _mind_ready(session, slug):
        return f"held seed {slug} wake {pending} mind busy"
    entries = Feed(home).entries()
    original = next(entry for entry in entries if entry.sequence == pending)
    wake_match = WAKE_RE.fullmatch(_receipt_line(original.body))
    target = f"{session}:{slug}.1"
    data = journal["data"] if journal else dict(role=slug, wake=pending,
        before=dict(pending_wake_matches=_state(home, slug)[1] == pending, mind_ready_observed=True,
                    readiness_probe="configured _mind_ready probe accepted this pane", prior_delivery_sequence=last_delivery_sequence,
                    prior_delivery_at=last_delivery.isoformat(), quiet_seconds=(datetime.now(timezone.utc)-last_delivery).total_seconds()),
        notification=dict(phase="planned", target=target, method="supervisor tmux paste/send",
                          send_commands_returned=False, mind_started_work_verified=False))
    planned = (f"seed redeliver {slug} wake={pending}\nThe exact prior wake remains unsettled after a quiet retry interval. "
               f"The configured readiness probe accepted {slug}'s pane; the supervisor plans to resend its original obligation. "
               f"{slug} must inspect this wake's artifacts and reconcile prior effects before any new bounded step. "
               "No notification success or work start is claimed by this plan.")
    if not journal:
        require(home, "seed", planned, context=episode(home, "seed", planned, context=data))
        if _state(home, slug)[1] != pending or not _mind_ready(session, slug):
            raise ValueError("pending wake/readiness changed during redelivery review; no notification sent")
    if not journal and wake_match and wake_match.group(4):
        plan = task_state.states(entries).get(wake_match.group(4))
        if plan is not None and plan.attempt_wake != pending:
            task_state.record_attempt(home, plan, pending, int(wake_match.group(2)), owner=slug)
    if not journal:
        restore_message = _restore_text(home, slug, session, wake_delivery=True) + "\n" + (
        f"REDELIVERY of unsettled WAKE {pending} for {slug}. A prior delivery may have been lost during clear. "
        "First inspect the live top pane, chat.log, and artifacts for this exact wake. "
        "Reconcile any prior effect before making another change. Then complete one bounded step, "
        f"write a handoff, and settle with seed yield --slug {slug} --wake {pending} --file HANDOFF_FILE "
        "--result changed|verified|blocked.\n") + "ORIGINAL OBLIGATION\n" + original.body + "\n"
        if _state(home, slug)[1] != pending or not _mind_ready(session, slug):
            raise ValueError("pending wake/readiness changed during redelivery preparation; no notification sent")
        journal = dict(phase="intent", data=data)
        _save(journal_path, journal)
        _send(target, restore_message)
        data = dict(data, notification=dict(data["notification"], phase="verified", send_commands_returned=True))
        journal.update(phase="completed", data=data)
        _save(journal_path, journal)
    final = (f"seed redeliver {slug} wake={pending}\nThe supervisor's tmux send commands returned successfully for this exact unsettled wake "
             "after the recorded quiet interval and accepted readiness probe. This establishes command delivery, not proof that the mind started work. "
             f"{slug} must inspect the original obligation and prior artifacts, reconcile earlier effects, then complete one bounded step and handoff.")
    def commit_guard():
        if _state(home, slug)[1] != pending:
            raise ValueError("exact pending wake changed before redelivery receipt; reconcile completed notification")
    receipt = Feed(home).append_record("seed", final, data, kind="redelivery", commit_guard=commit_guard)
    journal.update(phase="committed", receipt_sequence=receipt.sequence)
    _save(journal_path, journal)
    return f"redelivered seed {slug} wake {pending}"


def _restore_text(home: Path, slug: str, session: str, *, wake_delivery: bool = False) -> str:
    from . import wall
    if wall.enabled(home):
        return wall.restore(home, slug, session)
    from .runtime_source import source_for
    runtime = source_for(home, Path(__file__).resolve().parents[2])
    doctrine = _instruction_text(home, "doctrine.md", _core_doctrine(home))
    charter = _instruction_text(home, f"charters/{slug}.md", _core_charter(slug, home))
    handoff_path = home / "handoffs" / f"{slug}.md"
    handoff = handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else "(none yet)"
    pending = _state(home, slug)[1]
    next_action = (
        "The WAKE following this context is the current obligation. Reconcile the handoff and live pane before another effect."
        if wake_delivery else
        f"Wake {pending} is unsettled after restart. Inspect its artifact and current top pane before settling it; do not repeat an uncertain effect."
        if pending is not None else
        "Wait for an explicit WAKE before changing source. A restore alone is not a new obligation."
    )
    return (f"Read repository AGENTS.md for the agent contract.\nDOCTRINE\n{doctrine}\nCHARTER {slug}\n{charter}\nCURRENT HANDOFF\n{handoff}\n"
            f"WAKE STATE\n{status(home, slug)}\n"
            "TASK STATE\n" + "\n".join(task_state.board(Feed(home).entries())) + "\n"
            f"{next_action}\n"
            f"The canonical site is {home.resolve()}. The CLI resolves it automatically from MISHE_SEED_HOME, "
            "which fresh mind launches set to this path; if this older process lacks it, use this exact path "
            "in shell commands. Never retype the directory from memory.\n"
            f"The installed runtime source is {runtime}; the development checkout is {home.parent.resolve()}. "
            "Inspect the source that actually runs and the task's isolated candidate; development dirt is not deployed code. "
            f"Use the canonical CLI {home.resolve() / 'bin/mishe-tauftauf'} when present.\n"
            f"Read the complete current dashboard with mishe-tauftauf --home {shlex.quote(str(home))} pain read {slug} --launcher dashboard. "
            f"Verify presentation liveness separately with mishe-tauftauf --home {shlex.quote(str(home))} pain read {slug} --launcher tmux --session {shlex.quote(session)}. "
            "For an explicit wake, act on one bounded obligation, verify it on the same surface, and leave an artifact.\n")


def _core_doctrine(home: Path | None = None) -> str:
    from .runtime_source import package_for
    return (package_for(home, Path(__file__).resolve().parents[1]) / "mishe_tauftauf/seed_doctrine.md").read_text(encoding="utf-8")


def _core_charter(slug: str, home: Path | None = None) -> str:
    from .runtime_source import package_for
    source = package_for(home, Path(__file__).resolve().parents[1]) / "mishe_tauftauf" / f"seed_{slug}_charter.md"
    if source.exists():
        return source.read_text(encoding="utf-8")
    return (
        f"# {slug}\n\nGoal: maintain and develop this plant from live evidence.\n"
        "Read the full top pane and chat.log. Repair a RED or UNKNOWN check before adding work. "
        "When healthy, choose one bounded improvement, test it, and record the artifact. "
        "For repository changes, inspect the exact diff, seek independent review, commit only owned paths, "
        "push to this repository's configured GitHub origin, and record the SHA and push result. "
        "Never stage this plant site's chat, charters, handoffs, artifacts, plans, checks, or services. "
        "Keep the task open across handoffs until it lands; preserve other channels' dirty work. "
        "Use deterministic checks for facts; treat missing evidence as UNKNOWN. "
        "Write a handoff and settle the exact wake before clearing context. "
        "For unfinished long work, name the task and exact next step in the handoff, "
        "then use seed yield --continue so the supervisor wakes the next step after clear.\n"
    )


def _instruction_baselines(home: Path) -> tuple[Path, dict[str, str]]:
    path = home / "health" / "core-instruction-baselines.json"
    return path, json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sync_instruction(home: Path, relative: str, default: str) -> None:
    path = home / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path, baselines = _instruction_baselines(home)
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if (current is None or _digest(current) == baselines.get(relative) or current == default or
            _digest(current) in LEGACY_INSTRUCTION_HASHES.get(relative, set())):
        path.write_text(default, encoding="utf-8")
    baselines[relative] = _digest(default)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(baselines, sort_keys=True) + "\n", encoding="utf-8")


def _instruction_text(home: Path, relative: str, default: str) -> str:
    path = home / relative
    _, baselines = _instruction_baselines(home)
    if not path.exists():
        return default
    local = path.read_text(encoding="utf-8")
    if local == default or _digest(local) == baselines.get(relative):
        return default
    return default.rstrip() + "\n\nLOCAL SITE ADDITIONS\n" + local


def init(home: Path, slug: str, engine_command: str = "codex") -> str:
    from .cli import initialize

    slug = validate_slug(slug)
    _require_worktree(home)
    initialize(home)
    bin_dir = home / "bin"
    bin_dir.mkdir(exist_ok=True)
    cli = bin_dir / "mishe-tauftauf"
    if not cli.exists():
        package_root = str(Path(__file__).resolve().parents[1])
        cli.write_text(
            "#!/bin/sh\n"
            f"export PYTHONPATH={shlex.quote(package_root)}${{PYTHONPATH:+:$PYTHONPATH}}\n"
            f"exec {shlex.quote(sys.executable)} -m mishe_tauftauf \"$@\"\n",
            encoding="utf-8",
        )
        cli.chmod(0o755)
    if (home / "health/runtime-release.json").exists():
        from .runtime_source import refresh_cli, source_for
        refresh_cli(home, source_for(home, Path(__file__).resolve().parents[2]))
    _sync_instruction(home, "doctrine.md", _core_doctrine(home))
    _sync_instruction(home, f"charters/{slug}.md", _core_charter(slug, home))
    mind = home / "minds" / slug
    if not mind.exists():
        argv = shlex.split(engine_command)
        if not argv:
            raise ValueError("engine command is empty")
        mind.write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n", encoding="utf-8")
        mind.chmod(0o755)
    top = home / "top-pains" / slug
    if not top.exists():
        if slug == "witness":
            top.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                           " -m mishe_tauftauf.seed_witness_view --home " + shlex.quote(str(home.resolve())) + "\n",
                           encoding="utf-8")
        else:
            top.write_text(
            "#!/bin/sh\n"
            f"debt=$({shlex.quote(sys.executable)} -m mishe_tauftauf.landing_debt --home {shlex.quote(str(home.resolve()))} --repo {shlex.quote(str(home.parent.resolve()))} audit --intake 2>&1)\n"
            "printf '%s\\n' \"$debt\"\n"
            "printf '%s\\n' 'GOAL: keep a living, plantable genome that can observe, repair, and reproduce its own development loop'\n"
            "printf '%s\\n' 'PURSUIT: turn recurring blind spots into checks, carry long work through handoff and clear, and grow useful capability from evidence'\n"
            "printf '%s\\n' 'DESIRED STATE: checks are truthful and the next bounded improvement is visible'\n"
            f"doctor=$({shlex.quote(sys.executable)} -m mishe_tauftauf --home {shlex.quote(str(home.resolve()))} doctor 2>&1)\n"
            "rc=$?\n"
            f"mkdir -p {shlex.quote(str(home.resolve() / 'observations'))}\n"
            "if [ \"$rc\" -eq 0 ]; then "
            f"printf '%s\\n' 'PASS plant doctor' > {shlex.quote(str(home.resolve() / 'observations' / slug))}; "
            "else printf '%s\\n' \"$doctor\"; "
            f"printf '%s\\n' 'FAIL plant doctor' > {shlex.quote(str(home.resolve() / 'observations' / slug))}; fi\n"
            f"if status=$(git -C {shlex.quote(str(home.parent.resolve()))} status --short 2>/dev/null); then "
            "count=$(printf '%s\\n' \"$status\" | sed '/^$/d' | wc -l); "
            "printf 'WORKTREE: %s changed paths (full: git status --short)\\n' \"$count\"; "
            "printf '%s\\n' \"$status\" | head -n 5; "
            "else count=unknown; printf '%s\\n' 'WORKTREE: UNKNOWN — git status unavailable'; fi\n"
            f"if [ -x {shlex.quote(str(home.parent.resolve() / '.venv' / 'bin' / 'pytest'))} ]; then "
            "printf '%s\\n' 'TEST RUNNER: .venv/bin/pytest available'; "
            "else printf '%s\\n' 'TEST RUNNER: inspect project environment'; fi\n"
            f"{shlex.quote(sys.executable)} -c {shlex.quote(f'from pathlib import Path; from mishe_tauftauf.ci_watch import line; print(line(Path({str(home.resolve())!r})))')}\n"
            f"{shlex.quote(sys.executable)} -m mishe_tauftauf --home {shlex.quote(str(home.resolve()))} task landing-status\n"
            "printf 'GOAL: tend this repo · WORKTREE: %s changed\\n' \"$count\"\n"
            "debt_state=$(printf '%s\\n' \"$debt\" | sed -n 's/^LANDING DEBT: \\([^ ]*\\).*/\\1/p' | head -n 1)\n"
            "if [ \"$rc\" -eq 0 ]; then printf 'STATE: GREEN doctor · LANDING DEBT: %s\\n' \"${debt_state:-UNKNOWN}\"; "
            "else printf 'STATE: RED doctor · LANDING DEBT: %s\\n' \"${debt_state:-UNKNOWN}\"; fi\n",
            encoding="utf-8",
            )
        top.chmod(0o755)
    service = home / "mishe-seed.service"
    if not service.exists():
        service.write_text(
            "[Unit]\nDescription=Mishe self-tending seed\nAfter=default.target\n\n"
            "[Service]\nType=simple\n"
            f"WorkingDirectory={home.parent.resolve()}\n"
            f"ExecStart={sys.executable} -m mishe_tauftauf --home {home.resolve()} seed run --slug {slug}\n"
            "Restart=always\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
    return str(home)


def _mind_launch_argv(home: Path, slug: str) -> tuple[str, ...]:
    from .runtime_source import package_for, python_search_path
    from . import wall
    package_root = str(Path(__file__).resolve().parents[1] if wall.enabled(home)
                       else package_for(home, Path(__file__).resolve().parents[1]))
    python_path = python_search_path(package_root, os.environ.get("PYTHONPATH", ""))
    mind_path = os.pathsep.join((str(home / "bin"), os.environ.get("PATH", "/usr/bin:/bin")))
    return ("-c", str(home.parent.resolve()), "env", f"PATH={mind_path}",
            f"PYTHONPATH={python_path}",
            f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}",
            f"MISHE_SEED_HOME={home.resolve()}",
            str(home / "minds" / slug))


def start(home: Path, session: str, slug: str, interval: float) -> str:
    slug = validate_slug(slug)
    workspace = str(home.parent.resolve())
    if not executable(home / "top-pains" / slug) or not executable(home / "minds" / slug):
        raise ValueError(f"seed {slug} needs executable top-pains/{slug} and minds/{slug}")
    created = _tmux("has-session", "-t", session, check=False).returncode != 0
    if created:
        _tmux("new-session", "-d", "-s", session, "-n", slug, "-c", workspace, "sh")
        _tmux("set-option", "-t", session, OWNED_OPTION, str(home.resolve()))
        identity = _tmux("display-message", "-p", "-t", session, "#{session_id}").stdout.decode().strip()
        (home / ".seed-raised").write_text(f"{session} {identity}\n", encoding="utf-8")
    elif not owns_session(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    target = f"{session}:{slug}"
    names = _tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines()
    new_window = slug not in names
    if new_window:
        _tmux("new-window", "-d", "-t", session, "-n", slug, "-c", workspace, "sh")
    panes = _tmux("list-panes", "-t", target, "-F", "#{pane_index}").stdout.decode().splitlines()
    if "1" not in panes:
        _tmux("split-window", "-v", "-t", target, "-c", workspace, "sh")
    _tmux("set-option", "-p", "-t", f"{target}.0", "remain-on-exit", "on")
    _tmux("set-option", "-p", "-t", f"{target}.1", "remain-on-exit", "on")
    _tmux("select-layout", "-t", target, "even-vertical")
    from .runtime_source import package_for, python_search_path
    from . import wall
    package_root = str(Path(__file__).resolve().parents[1] if wall.enabled(home)
                       else package_for(home, Path(__file__).resolve().parents[1]))
    python_path = python_search_path(package_root, os.environ.get("PYTHONPATH", ""))
    top_cmd = ("env", f"MISHE_SEED_SESSION={session}", f"PYTHONPATH={python_path}",
               *_python_command("--home", str(home), "pain", "watch", slug, "--interval", str(interval)))
    top_dead = _tmux("display-message", "-p", "-t", f"{target}.0", "#{pane_dead}").stdout.decode().strip() == "1"
    if created or new_window or top_dead:
        _tmux("respawn-pane", "-k", "-t", f"{target}.0", *top_cmd)
    mind_dead = _tmux("display-message", "-p", "-t", f"{target}.1", "#{pane_dead}").stdout.decode().strip() == "1"
    if created or new_window or mind_dead:
        _tmux("respawn-pane", "-k", "-t", f"{target}.1", *_mind_launch_argv(home, slug))
        time.sleep(2.0)
        if _state(home, slug)[1] is not None:
            _send(f"{target}.1", _restore_text(home, slug, session))
    return f"seed {slug} ready in {session}"


def stop(home: Path, session: str) -> str:
    if _tmux("has-session", "-t", session, check=False).returncode != 0:
        return f"seed session {session} absent"
    if not owns_session(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    identity = _tmux("display-message", "-p", "-t", session, "#{session_id}").stdout.decode().strip()
    receipt = home / ".seed-raised"
    if not receipt.is_file() or receipt.read_text(encoding="utf-8") != f"{session} {identity}\n":
        raise ValueError(f"session {session} was not raised by this seed; refusing to kill it")
    _tmux("kill-session", "-t", session)
    receipt.unlink()
    return f"seed session {session} stopped"


def tick(home: Path, session: str, slug: str, self_pick_seconds: float = 0) -> str:
    slug = validate_slug(slug)
    from . import wall
    if wall.enabled(home):
        from .tmux import TmuxError
        try:
            return wall.tick(home, session, slug, self_pick_seconds)
        except (OSError, ValueError, TmuxError) as exc:
            return f"UNKNOWN seed {slug} wall transport/observation: {exc}"
    if not owns_session(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    with _lock(home):
        if slug in RENEWAL_SLUGS:
            discovery.renew_scan(home)
        if _pane_stopped_or_dead(session, slug):
            return f"UNKNOWN seed {slug} top pane stopped or dead"
        full = capture_raw(session, slug)
        if full.startswith("UNKNOWN — top-pain") or "-- pane live " not in full:
            return f"UNKNOWN seed {slug} top pane unavailable"
        lease = lease_value(full)
        if lease is None:
            return f"UNKNOWN seed {slug} top pane has no valid lease"
        try:
            stamp = lease.removeprefix("-- pane live ").split(" ·", 1)[0]
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            return f"UNKNOWN seed {slug} top pane lease malformed"
        if not 0 <= age <= 30:
            return f"UNKNOWN seed {slug} top pane lease stale ({age:.1f}s)"
        # The pane proves display liveness. Its viewport is not semantic evidence.
        from .dashboard import read as read_dashboard
        try:
            full, dashboard_ok = read_dashboard(home, slug)
        except ValueError as exc:
            return f"UNKNOWN seed {slug} {exc}"
        frame = strip_owned_chrome(full).strip()
        if not frame:
            return f"UNKNOWN seed {slug} top pane empty"
        digest = hashlib.sha256(_observation_text(slug, frame).encode("utf-8")).hexdigest()
        previous, pending, last_yield, last_clear, last_observation, last_woken_observation, last_wake_at, continue_yield = _state(home, slug)
        changed = digest != previous
        if changed:
            state_line = next((line for line in frame.splitlines() if line.startswith("STATE:")), "state not stated")
            previous_entry = next((entry for entry in Feed(home).entries() if entry.sequence == last_observation), None)
            previous_data = record_payload(previous_entry) if previous_entry and previous_entry.body.splitlines()[0] == f"seed observation {slug}" else {}
            previous_state = previous_data.get("state", "previous state not recorded")
            # Only fixed vocabulary derived from check labels enters prose.
            # Arbitrary dashboard/chat text, including JSON, stays in the record.
            states = lambda text: (re.search(r'\b(GREEN|RED|UNKNOWN)\b', text).group(1)
                                   if re.search(r'\b(GREEN|RED|UNKNOWN)\b', text) else "UNKNOWN")
            current_state = states(state_line)
            old_state = states(previous_state) if re.search(r'\b(GREEN|RED|UNKNOWN)\b', previous_state) else "not recorded"
            summaries = []
            for prefix in ("CI:", "COORDINATION:", "ANOMALY:", "PUBLICATION GATE:", "PUBLICATION RESULT:"):
                statuses = [re.search(r'\b(PASS|FAIL|RED|GREEN|UNKNOWN|SUSPICIOUS|CLEAR|UNTESTED|READY|WAITING)\b', line[len(prefix):], re.I)
                            for line in frame.splitlines() if line.startswith(prefix)]
                labels = sorted({match.group(1).upper() for match in statuses if match})
                if labels:
                    summaries.append(prefix.rstrip(":").lower() + " " + "/".join(labels))
            details = "; ".join(summaries) or "the recorded dashboard check states"
            from .post_check import _deterministic
            findings = [line for line in _observation_text(slug, frame).splitlines()
                        if line.startswith(("ANOMALY:", "COORDINATION:", "CI:", "PUBLICATION RESULT:", "STATE:"))
                        and "```" not in line and not _deterministic(line)]
            # Select whole findings, never clip a finding or put JSON in chat.
            findings.sort(key=lambda line: (not bool(re.search(r'\b(RED|UNKNOWN|FAIL|SUSPICIOUS)\b', line, re.I)), line))
            finding_text = "\nChecked findings:\n" + "\n".join(findings[:3]) if findings else ""
            action = (f"{slug} should advance ready owned work; healthy checks do not complete open tasks."
                      if current_state == "GREEN" else
                      f"{slug} should diagnose failing or unavailable checks in this snapshot, reconcile prior effects, and record a bounded repair or an exact producer/retry wait.")
            prior_snapshot_available = bool(previous_data.get("snapshot"))
            if previous and prior_snapshot_available:
                change = "scoped check signature differs"
                change_note = "The scoped check signature differs from the previous recorded observation. "
                comparison_note = "The immutable record preserves the exact scoped view and prior snapshot for comparison. "
            elif previous:
                change = "current scoped baseline; prior semantic comparison unavailable"
                change_note = ("The earlier observation has no recorded semantic snapshot. "
                               "This records the current scoped baseline. No semantic change from the earlier observation is verified. ")
                comparison_note = "The immutable record preserves the exact current scoped view and the available earlier receipt facts. "
            else:
                change = "first recorded scoped check snapshot"
                change_note = "This is the first recorded scoped check snapshot; no earlier observation was recorded. "
                comparison_note = "The immutable record preserves the exact current scoped view. "
            explanation = (f"Seed checked the fresh {slug} pane lease and read its full dashboard data. " + change_note +
                f"Previous state {old_state}, current state {current_state}. "
                f"Current check summaries: {details}. "
                "These current checks determine which owned step is admissible. " + comparison_note +
                "No task completion or repair progress is claimed by this observation. " + action + finding_text)
            snapshot = dict(meaningful_text=_observation_text(slug, frame), state=state_line,
                            origin="dashboard.read", lease_checked=True, lease_stamp=stamp,
                            lease_age_seconds=age, dashboard_command_ok=dashboard_ok, viewport_is_semantic_evidence=False)
            observation = Feed(home).append_record("seed", f"seed observation {slug}\n" + explanation,
                {"digest": digest, "role": slug, "state": state_line, "snapshot": snapshot,
                 "previous": dict(sequence=last_observation, digest=previous, state=previous_state,
                                  snapshot=previous_data.get("snapshot"), snapshot_available=bool(previous_data.get("snapshot"))),
                 "change": change,
                 "next_owner": slug, "next_action": action}, kind="observation")
            last_observation = observation.sequence
        if pending is not None:
            return _redeliver_pending(home, session, slug, pending)
        if last_yield is not None and last_clear != last_yield:
            return f"held seed {slug} yield {last_yield} awaiting clear"
        continue_due = continue_yield is not None and continue_yield == last_yield
        self_pick_due = (self_pick_seconds > 0 and last_wake_at is not None
                         and (datetime.now(timezone.utc) - last_wake_at).total_seconds() >= self_pick_seconds)
        external = _external_event(home, slug)
        entries = Feed(home).entries()
        plans = list(task_state.states(entries).values())
        available = task_state.candidates(entries)
        choice_signature = hashlib.sha256(json.dumps([
            (p.identity, p.sequence) for p in available], sort_keys=True).encode()).hexdigest()
        waiting_signature = hashlib.sha256(json.dumps([
            (p.identity, p.next_step, p.reason, p.retry_task, p.retry_event, p.retry_at)
            for p in plans], sort_keys=True).encode()).hexdigest()
        last_choice = {}
        for entry in reversed(entries):
            if entry.source == "seed" and entry.body.startswith(f"seed wake {slug} "):
                if any(line.startswith("[record] ") for line in entry.body.splitlines()):
                    last_choice = record_payload(entry)
                break
        choice_due = bool(available) and choice_signature != last_choice.get("choice_signature")
        opportunity = None
        if plans and not available:
            opportunity = waiting_signature if self_pick_due and waiting_signature != last_choice.get("waiting_signature") else None
            self_pick_due = bool(opportunity)
            continue_due = False
            if last_observation == last_woken_observation and external is None and opportunity is None:
                return f"waiting seed {slug} task prerequisites unchanged"
        if last_observation is None or (last_observation == last_woken_observation and not self_pick_due and external is None and not continue_due and not choice_due):
            return f"quiet seed {slug} unchanged"
        mind_dead = _tmux("display-message", "-p", "-t", f"{session}:{slug}.1", "#{pane_dead}", check=False)
        if mind_dead.returncode or mind_dead.stdout.decode().strip() == "1":
            return f"HOLD seed {slug} resident mind absent"
        if not _mind_ready(session, slug):
            return f"HOLD seed {slug} resident mind busy"
        event_suffix = f" event={external.sequence}" if external is not None else ""
        reason = ("changed shared task board; choose useful work" if choice_due else
                  f"addressed chat event {external.sequence}" if external is not None else
                  "continued task" if continue_due else "quiet self-pick" if self_pick_due and not changed else
                  "current scoped observation baseline; prior semantic comparison unavailable"
                  if changed and not prior_snapshot_available else "changed top-pane observation")
        wake = Feed(home).append_record("seed", f"seed wake {slug} observation={last_observation}{event_suffix}\n"
                                 f"The supervisor woke {slug} because of {reason}. "
                                 "The mind chooses its own useful step and atomically claims it before acting. "
                                 "It must leave a checked handoff and settle this exact wake." +
                                 ("\nINDEPENDENT WORK OPPORTUNITY: leave blocked acceptance waiting; "
                                  "produce an admissible child measurement, repair, analysis or limitations draft."
                                  if opportunity else ""),
                                 {"role": slug, "observation": last_observation, "reason": reason,
                                  "choice_signature": choice_signature, "waiting_signature": waiting_signature}, kind="wake")
        prompt = (f"WAKE {wake.sequence} for {slug}. Read your live top pane now. Current observation:\n{frame}\n"
                  + "MIND SELECTS: choose an admissible useful task, including related work from another role. "
                  "Check prior effects and producer completion, then run "
                  f"mishe-tauftauf --home {shlex.quote(str(home))} task claim TASK_ID --owner {slug} "
                  f"--wake {wake.sequence} --reason 'why this step is useful' --evidence SELECTION_EVIDENCE. "
                  "A refused claim means reconcile before acting. No helper offer is required. "
                  "Record checked progress with task step or an exact prerequisite with task wait before yield.\n"
                  + "\n".join(task_state.board(entries)) + "\n"
                  + (f"NEW CHAT EVENT {external.sequence} from {external.source}: {external.body[:4096]}\n" if external is not None else "") +
                  ("INDEPENDENT WORK OPPORTUNITY: all recorded tasks are waiting. Choose one admissible "
                   "evidence-producing child or repair within your scope; leave the blocked acceptance waiting. "
                   "This opportunity is consumed once for these waiting inputs; an unchanged audit is not progress.\n"
                   if opportunity else "") +
                  "WORK SELECTION: final acceptance being blocked does not prohibit independently admissible work. "
                  "Choose a step that produces missing evidence, a bounded measurement, a repair or a limitations draft. "
                  "For a waiting goal, add an owned child with task add --parent and record it as task wait --alternative. "
                  "Do not repeat an unchanged absence audit or wait for a locally owned deliverable to appear. "
                  "Keep frozen registrations, resource budgets and external authority boundaries intact. "
                  "Review readiness should wait on the producer with task wait --retry-task, without --continue polling.\n" +
                  ("SELF-PICK: the pane is stable. Derive one bounded improvement from your charter goal or an open wish.\n" if self_pick_due and not changed else "") +
                  (f"CONTINUE TASK from wake {continue_yield}: read the restored handoff, verify prior effects, then take its next bounded step.\n" if continue_due else "") +
                  "Choose one bounded action, verify the same check, write an artifact, then run "
                  f"mishe-tauftauf --home {shlex.quote(str(home))} seed yield --slug {slug} "
                  f"--wake {wake.sequence} --file HANDOFF_FILE --result changed|verified|blocked. "
                  "Add --continue if the task has another checked step.\n")
        _send(f"{session}:{slug}.1", _restore_text(home, slug, session, wake_delivery=True) + "\n" + prompt)
        return f"wake seed {slug} {wake.sequence}"


def _handoff_matches(path: Path, digest: str) -> bool:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest


def _write_handoff(path: Path, text: str) -> None:
    """Durable atomic text replacement, without repeating an identical effect."""
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _yield_guards(home: Path, slug: str, wake: int, digest: str, archive: Path,
                  work_prefix: str, work_data: dict, *, require_work: bool) -> dict:
    current = home / "handoffs" / f"{slug}.md"
    if not _handoff_matches(archive, digest) or not _handoff_matches(current, digest):
        raise ValueError("archive/current handoff bytes do not match the admitted source; reconcile before yielding")
    entries = Feed(home).entries()
    receipts = [entry for entry in entries if entry.source == "seed" and entry.body.startswith(work_prefix)]
    if require_work and (len(receipts) != 1 or record_payload(receipts[0]) != work_data):
        raise ValueError("work receipt does not match admitted handoff; reconcile before yielding")
    return dict(archive_bytes_match_source=True, current_handoff_bytes_match_source=True,
                archive=str(archive), current_handoff=str(current), handoff_sha256=digest,
                work_receipt_matches=bool(receipts), work_receipt_sequence=receipts[0].sequence if receipts else None)


def yield_wake(home: Path, slug: str, wake: int, handoff_file: Path, continue_task: bool = False,
               result: str = "unspecified") -> str:
    slug = validate_slug(slug)
    handoff_text = handoff_file.read_bytes().decode("utf-8")
    if not handoff_text.strip():
        raise ValueError("handoff is empty")
    if result not in {"changed", "verified", "blocked", "unspecified"}:
        raise ValueError("result must be changed, verified, or blocked")
    from . import wall
    if wall.enabled(home):
        return wall.settle(home, slug, wake, handoff_text, continue_task, result)
    with _lock(home), publication_lock(home):
        _, pending, last_yield, _, _, observation, _, _ = _state(home, slug)
        if pending != wake:
            # Reconcile a committed receipt whose final private journal write
            # was interrupted. No new publication or handoff effect is repeated.
            recovery_path = home / "checks" / f"yield-{slug}-{wake}.json"
            recovery = json.loads(recovery_path.read_text()) if recovery_path.exists() else None
            expected_digest = hashlib.sha256(handoff_text.encode("utf-8")).hexdigest()
            if (pending is None and last_yield == wake and recovery
                    and recovery.get("handoff_sha256") == expected_digest
                    and recovery.get("result") == result and recovery.get("continue_task") == continue_task):
                committed = [entry for entry in Feed(home).entries() if entry.source == "seed"
                             and entry.body == recovery.get("yield_body")]
                if len(committed) != 1:
                    raise ValueError(f"yield recovery lacks its exact canonical receipt; reconcile {recovery_path}")
                _yield_guards(home, slug, wake, expected_digest, Path(recovery["archive"]),
                              f"[work] channel={slug} wake={wake} ", recovery["work_data"], require_work=True)
                from .post_check import _save
                recovery.update(phase="committed", yield_sequence=committed[0].sequence)
                _save(recovery_path, recovery)
                return f"yield seed {slug} {wake}"
            raise ValueError(f"wake {wake} is not the pending wake for {slug}")
        entries = Feed(home).entries()
        original = next(entry for entry in entries if entry.sequence == wake)
        wake_match = WAKE_RE.fullmatch(_receipt_line(original.body))
        observation = int(wake_match.group(2)) if wake_match else observation
        selected_task = task_state.pending_tasks(entries).get((slug, wake)) or (wake_match.group(4) if wake_match else None)
        selected_state = task_state.registry(entries).get(selected_task) if selected_task else None
        if continue_task and selected_task and (selected_state is None or selected_state.status in {"done", "dropped"}
                                               or not task_state.eligible(selected_state, entries)):
            raise ValueError("continuation requires a checked next step or fired retry; record task step or task wait")
        archive = home / "artifacts" / f"seed-{slug}-wake-{wake}.md"
        if archive.exists() and archive.read_bytes().decode("utf-8") != handoff_text:
            raise ValueError(f"archived handoff for wake {wake} differs; reconcile before yielding")
        digest = hashlib.sha256(handoff_text.encode("utf-8")).hexdigest()
        excerpt = handoff_text.strip()[:4096]
        if len(handoff_text.strip()) > 4096:
            excerpt += "\n[truncated; read archived handoff]"
        work_prefix = f"[work] channel={slug} wake={wake} "
        header = (work_prefix + f"observation={observation or 'none'} "
                  f"result={result} continue={int(continue_task)}")
        work_data = {"channel": slug, "wake": wake, "observation": observation, "result": result,
                     "continue": continue_task, "handoff_sha256": digest, "archive": str(archive)}
        if selected_task:
            header += f" task={selected_task}"
            work_data["task"] = selected_task
            if selected_state:
                work_data["task_state"] = selected_state.sequence
                work_data["task_step"] = hashlib.sha256((selected_state.next_step + "\0" + selected_state.evidence_sha256).encode()).hexdigest()
                work_data["task_outcome"] = hashlib.sha256((selected_state.progress + "\0" + selected_state.reason).encode()).hexdigest()
                work_data["next_step"] = selected_state.next_step
                work_data["reason"] = selected_state.reason
                work_data["progress"] = selected_state.progress
                work_data["retry_task"] = selected_state.retry_task
                work_data["retry_event"] = selected_state.retry_event
                work_data["retry_at"] = selected_state.retry_at
                work_data["evidence"] = selected_state.evidence
                work_data["evidence_sha256"] = selected_state.evidence_sha256
        work_body = f"{header}\n{slug} reports {result} for " + (f"task {selected_task}" if selected_task else "this investigation") + \
                    f". Read the checked outcome and next action below; full handoff: {archive}.\nHANDOFF:\n{excerpt}"
        yield_body = f"seed yield {slug} wake={wake}" + (" continue=1" if continue_task else "") + \
                     "\nThis receipt settles the exact wake after preserving its verified handoff and work receipt. " + \
                     ((f"Task {selected_task} continues after context clear." if selected_task else "This investigation continues after context clear.")
                      if continue_task else "No continuation was requested.")
        if selected_state:
            if not continue_task:
                yield_body += f" Task {selected_task} remains {selected_state.status}."
            if selected_state.status not in {"done", "dropped"} and selected_state.next_step:
                yield_body += f" Next recorded action: {selected_state.next_step}"
        planned_yield_body = yield_body.replace(
            "This receipt settles the exact wake after preserving its verified handoff and work receipt.",
            "The supervisor plans to settle this exact wake. It will publish a settlement receipt only after checking the admitted handoff files, exactly one matching canonical work receipt, and the still-pending wake.", 1)
        prior = [entry for entry in Feed(home).entries() if entry.source == "seed" and
                 entry.body.startswith(work_prefix)]
        if prior:
            if len(prior) != 1 or prior[0].body.splitlines()[0] != header or record_payload(prior[0]) != work_data:
                raise ValueError(f"work receipt for wake {wake} differs; reconcile before yielding")
        from .post_check import require
        from .records import prepare
        from .coordination_checks import episode
        work_body += "\n" + prepare(home, work_data, kind="work")
        journal_path = home / "checks" / f"yield-{slug}-{wake}.json"
        from .post_check import _save
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else None
        identity = dict(role=slug, wake=wake, handoff_sha256=digest, result=result,
                        continue_task=continue_task, work_data=work_data, yield_body=yield_body)
        if journal and any(journal.get(key) != value for key, value in identity.items()):
            raise ValueError(f"yield transaction differs; reconcile {journal_path} before retry")
        transaction = dict(event="yield", phase="prepared", role=slug, wake=wake,
            before=dict(pending_wake_matches=True, handoff_source=str(handoff_file),
                        handoff_source_sha256=digest, handoff_source_read=True),
            required_commit_guards=["archive bytes match admitted source", "current handoff bytes match admitted source",
                                    "one matching canonical work receipt exists", "the exact wake remains pending"],
            expected_effect=dict(canonical_receipt_settles_wake=wake, continue_requested=continue_task),
            guard_semantics="Prepared transaction: required guards are code-enforced conditions before publication, not claims that future effects already happened.")
        publication_data = dict(work_data, transaction=transaction, admitted_handoff_text=handoff_text)
        work_transaction = dict(transaction, event="work",
            required_commit_guards=["archive bytes match admitted source", "current handoff bytes match admitted source",
                                    "the exact wake remains pending", "handoff source and selected task version unchanged"],
            expected_effect=dict(creates_work_receipt=True, work_wake=wake, settles_wake=False),
            guard_semantics="This publication creates the canonical work receipt after checking its handoff files. No prior work receipt is required. The later yield separately requires exactly one matching committed work receipt before settling the wake.")
        handoff_context = episode(home, slug, handoff_text, context=publication_data)
        work_context = episode(home, "seed", work_body, context=dict(publication_data, transaction=work_transaction))
        yield_context = episode(home, "seed", planned_yield_body, context=publication_data)
        if not prior:
            require(home, slug, handoff_text, context=handoff_context, stage="handoff")
            require(home, "seed", work_body, context=work_context)
        # Check the conditional settlement before effects. A recovery with an
        # already committed work receipt checks only the remaining publication.
        require(home, "seed", planned_yield_body, context=yield_context)
        if not _handoff_matches(handoff_file, digest):
            raise ValueError("handoff changed during publication review; resubmit the exact source")
        if not journal:
            journal = dict(identity, phase="prepared", archive=str(archive), work_body=work_body, yield_body=yield_body)
            _save(journal_path, journal)
        path = home / "handoffs" / f"{slug}.md"
        def commit_yield_guard(require_work: bool) -> None:
            _yield_guards(home, slug, wake, digest, archive, work_prefix, work_data, require_work=require_work)
            current_entries = Feed(home).entries()
            current_selected = task_state.registry(current_entries).get(selected_task) if selected_task else None
            if (_state(home, slug)[1] != wake or not _handoff_matches(handoff_file, digest)
                    or (selected_state is not None and (current_selected is None or current_selected.sequence != selected_state.sequence))):
                raise ValueError("yield source/wake/task version changed during review; reconcile without repeating effects")

        _write_handoff(path, handoff_text)
        if not archive.exists():
            _write_handoff(archive, handoff_text)
        guarded = _yield_guards(home, slug, wake, digest, archive, work_prefix, work_data, require_work=False)
        journal.update(phase="files-verified", postconditions=guarded)
        _save(journal_path, journal)
        if not prior:
            work_postconditions = {key: value for key, value in guarded.items() if not key.startswith("work_receipt_")}
            verified_work = dict(publication_data, transaction=dict(work_transaction, phase="verified", postconditions=work_postconditions))
            verified_work_context = episode(home, "seed", work_body, context=verified_work)
            require(home, "seed", work_body, context=verified_work_context)
            commit_yield_guard(False)
            Feed(home).append("seed", work_body, context=verified_work_context, commit_guard=lambda: commit_yield_guard(False))
        guarded = _yield_guards(home, slug, wake, digest, archive, work_prefix, work_data, require_work=True)
        if _state(home, slug)[1] != wake:
            raise ValueError("exact wake changed before settlement; reconcile transaction")
        journal.update(phase="work-verified", postconditions=guarded)
        _save(journal_path, journal)
        verified_yield = dict(publication_data, transaction=dict(transaction, phase="verified", postconditions=guarded))
        verified_yield_context = episode(home, "seed", yield_body, context=verified_yield)
        require(home, "seed", yield_body, context=verified_yield_context)
        commit_yield_guard(True)
        Feed(home).append("seed", yield_body, context=verified_yield_context, commit_guard=lambda: commit_yield_guard(True))
        _, remaining, last_yield, _, _, _, _, _ = _state(home, slug)
        if remaining is not None or last_yield != wake:
            raise ValueError("canonical yield receipt did not settle the exact wake; reconcile transaction")
        journal.update(phase="committed", yield_sequence=Feed(home).tail_sequence())
        _save(journal_path, journal)
        return f"yield seed {slug} {wake}"


def clear(home: Path, session: str, slug: str) -> str:
    slug = validate_slug(slug)
    from . import wall
    if wall.enabled(home):
        return wall.clear(home, session, slug)
    if not owns_session(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    with _lock(home), publication_lock(home):
        _, pending, last_yield, last_clear, _, _, _, _ = _state(home, slug)
        if pending is not None:
            raise ValueError(f"wake {pending} remains unsettled")
        if last_yield is not None and last_yield == last_clear:
            recovery_path = home / "checks" / f"clear-{slug}-{last_yield}.json"
            recovery = json.loads(recovery_path.read_text()) if recovery_path.exists() else None
            if recovery and recovery.get("session") == session and recovery.get("phase") in {"completed", "committed"}:
                target = f"{session}:{slug}.1"
                current_pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip()
                current_dead = _tmux("display-message", "-p", "-t", target, "#{pane_dead}").stdout.decode().strip()
                if (current_pid != recovery.get("new_pid") or current_dead != "0"
                        or not _handoff_matches(home / "handoffs" / f"{slug}.md", recovery.get("handoff_sha256", ""))):
                    raise ValueError(f"committed clear live effect differs; reconcile {recovery_path}")
                receipts = [entry for entry in Feed(home).entries() if entry.source == "seed"
                            and _receipt_line(entry.body) == f"seed clear {slug} after={last_yield}"]
                if len(receipts) != 1:
                    raise ValueError(f"clear recovery lacks its exact receipt; reconcile {recovery_path}")
                data = record_payload(receipts[0])
                observed = data.get("transaction", {}).get("postconditions", {})
                if observed.get("old_pid") != recovery.get("old_pid") or observed.get("new_pid") != current_pid:
                    raise ValueError(f"clear receipt differs from its observed effect; reconcile {recovery_path}")
                from .post_check import _save
                recovery.update(phase="committed", receipt_sequence=receipts[0].sequence)
                _save(recovery_path, recovery)
                return f"clear seed {slug} after {last_yield}"
        if last_yield is None or last_yield == last_clear:
            raise ValueError("no newly settled wake to clear")
        if not (home / "handoffs" / f"{slug}.md").is_file():
            raise ValueError("handoff missing")
        target = f"{session}:{slug}.1"
        dead = _tmux("display-message", "-p", "-t", target, "#{pane_dead}").stdout.decode().strip()
        if dead == "1":
            raise ValueError("mind pane is dead")
        if not _mind_idle(session, slug):
            raise ValueError("mind has not reached a stable idle boundary")
        clear_body = f"seed clear {slug} after={last_yield}\n" + (
            "The supervisor rotated the idle mind to a fresh process in the same pane. "
            "Its charter and latest handoff will be delivered with the next wake.")
        planned_body = f"seed clear {slug} after={last_yield}\n" + (
            "The supervisor plans to rotate the settled, idle mind in the same pane. "
            "It will verify a different live process before publishing the clear receipt; "
            "the next wake will deliver the charter and handoff.")
        from .coordination_checks import episode
        from .post_check import require, _save
        from .records import prepare
        effect_path = home / "checks" / f"clear-{slug}-{last_yield}.json"
        current_pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip()
        effect = json.loads(effect_path.read_text()) if effect_path.exists() else None
        if effect and (effect.get("session") != session or effect.get("body") != clear_body):
            raise ValueError(f"clear effect identity differs; reconcile {effect_path}")
        handoff = home / "handoffs" / f"{slug}.md"
        handoff_digest = hashlib.sha256(handoff.read_bytes()).hexdigest()
        transaction = dict(event="clear", phase="prepared", role=slug, settled_wake=last_yield,
            before=dict(session_owned=True, exact_wake_settled=True, mind_idle_verified=True,
                        pane_alive=True, old_pid=effect.get("old_pid") if effect else current_pid,
                        handoff=str(handoff), handoff_sha256=handoff_digest),
            expected_effect=dict(target=target, replace_process=True, next_wake_restores_charter_and_handoff=True),
            required_commit_guards=["new process differs from old process", "new pane is alive", "handoff bytes unchanged"],
            guard_semantics="Prepared transaction: rotation has not been claimed complete; actual observed postconditions are required before the receipt.")
        if effect and effect.get("phase") in {"completed", "committed"}:
            if current_pid != effect.get("new_pid"):
                raise ValueError(f"completed clear has a different live process; reconcile {effect_path}")
        else:
            if effect and current_pid != effect.get("old_pid"):
                raise ValueError(f"clear effect is uncertain after process change; reconcile {effect_path} before retry")
            if not current_pid:
                raise ValueError("mind process identity is missing")
            require(home, "seed", planned_body, context=episode(home, "seed", planned_body, context={"transaction":transaction}))
            # Recheck all live mutation preconditions after potentially slow model review.
            if (not owns_session(home, session) or _state(home, slug)[1] is not None or _state(home, slug)[2] != last_yield
                    or not _mind_idle(session, slug)
                    or _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip() != current_pid
                    or not _handoff_matches(handoff, handoff_digest)):
                raise ValueError("clear preconditions changed during review; resubmit without rotating")
            effect = dict(session=session, body=clear_body, old_pid=current_pid, phase="intent", handoff_sha256=handoff_digest)
            _save(effect_path, effect)
            _tmux("respawn-pane", "-k", "-t", target, *_mind_launch_argv(home, slug))
            time.sleep(0.5)
            new_pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip()
            new_dead = _tmux("display-message", "-p", "-t", target, "#{pane_dead}").stdout.decode().strip()
            if not new_pid or new_pid == current_pid or new_dead != "0":
                raise ValueError(f"mind rotation could not be verified; reconcile {effect_path}")
            effect.update(phase="completed", new_pid=new_pid)
            _save(effect_path, effect)
        # Recovery never rotates an already verified new process a second time.
        new_pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip()
        new_dead = _tmux("display-message", "-p", "-t", target, "#{pane_dead}").stdout.decode().strip()
        if (new_pid != effect.get("new_pid") or new_pid == effect.get("old_pid") or new_dead != "0"
                or not _handoff_matches(handoff, effect.get("handoff_sha256", handoff_digest))):
            raise ValueError(f"mind rotation postconditions no longer hold; reconcile {effect_path}")
        observed = dict(process_changed=True, pane_alive=True, old_pid=effect["old_pid"], new_pid=new_pid,
                        same_pane=target, handoff_bytes_unchanged=True, verification="tmux pane_pid/pane_dead and exact handoff hash checked before receipt")
        transaction = dict(transaction, phase="verified", postconditions=observed)
        final_data = dict(role=slug, settled_wake=last_yield, transaction=transaction)
        clear_body += "\n" + prepare(home, final_data, kind="clear")
        verified_clear_context = episode(home, "seed", clear_body, context=final_data)
        require(home, "seed", clear_body, context=verified_clear_context)
        def commit_clear_guard() -> None:
            state = _state(home, slug)
            if (not owns_session(home, session) or state[1] is not None or state[2] != last_yield
                    or _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip() != new_pid
                    or _tmux("display-message", "-p", "-t", target, "#{pane_dead}").stdout.decode().strip() != "0"
                    or not _handoff_matches(handoff, effect.get("handoff_sha256", handoff_digest))):
                raise ValueError(f"clear postconditions changed during final review; reconcile {effect_path}")
        commit_clear_guard()
        Feed(home).append("seed", clear_body, context=verified_clear_context, commit_guard=commit_clear_guard)
        effect.update(phase="committed", receipt_sequence=Feed(home).tail_sequence())
        _save(effect_path, effect)
        return f"clear seed {slug} after {last_yield}"


def status(home: Path, slug: str) -> str:
    digest, pending, last_yield, last_clear, _, _, _, continue_yield = _state(home, validate_slug(slug))
    return (f"seed {slug}: observation={digest or 'none'} pending={pending or 'none'} "
            f"yield={last_yield or 'none'} clear={last_clear or 'none'} "
            f"continue={continue_yield or 'none'}")


def follow(home: Path, session: str, slug: str, interval: float, self_pick_seconds: float = 3600) -> None:
    if interval <= 0:
        raise ValueError("interval must be positive")
    while True:
        print(tick(home, session, slug, self_pick_seconds), flush=True)
        time.sleep(interval)


def _clear_due(home: Path, slug: str, grace_seconds: float) -> bool:
    _, pending, last_yield, last_clear, _, _, _, _ = _state(home, slug)
    if pending is not None or last_yield is None or last_yield == last_clear:
        return False
    for entry in reversed(Feed(home).entries()):
        if entry.source == "seed" and YIELD_RE.fullmatch(_receipt_line(entry.body)) and entry.body.startswith(f"seed yield {slug} wake={last_yield}"):
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))).total_seconds()
            return age >= grace_seconds
    return False


def _mind_idle(session: str, slug: str) -> bool:
    if not _mind_ready(session, slug):
        return False
    target = f"{session}:{slug}.1"
    first = _tmux("capture-pane", "-p", "-t", target, check=False)
    if first.returncode:
        return False
    time.sleep(0.5)
    second = _tmux("capture-pane", "-p", "-t", target, check=False)
    return second.returncode == 0 and first.stdout == second.stdout


def run(home: Path, session: str, slug: str, interval: float, self_pick_seconds: float,
        clear_grace_seconds: float = 15) -> None:
    if interval <= 0:
        raise ValueError("interval must be positive")
    if clear_grace_seconds < 0:
        raise ValueError("clear grace must be nonnegative")
    while True:
        start(home, session, slug, interval)
        # A semantic checker outage (for example an unavailable model) is a
        # HOLD, not a fatal error. Let the resident supervisor keep tending
        # instead of exiting and letting systemd restart it in a tight loop.
        try:
            print(tick(home, session, slug, self_pick_seconds), flush=True)
        except ValueError as exc:
            print(f"HOLD seed {slug} tick: {exc}", flush=True)
        if _clear_due(home, slug, clear_grace_seconds) and _mind_idle(session, slug):
            try:
                print(clear(home, session, slug), flush=True)
            except ValueError as exc:
                print(f"HOLD seed {slug} clear: {exc}", flush=True)
        time.sleep(interval)
