"""Small tmux and text loop for a resident, self-tending channel."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed
from .records import payload as record_payload
from . import discovery
from .observations import (executable, redact_display_age, redact_redmi_cursor,
                           strip_owned_chrome, validate_home, validate_slug)
from .tmux import OWNED_OPTION, _pane_stopped_or_dead, _python_command, _session_socket, _socket_for_home, _tmux, await_owned, capture_raw, lease_value, owns_session


def _newest_scan_observations(home: Path) -> tuple[list | None, str | None]:
    """Newest discovery scan's observations, or (None, reason) when unusable.

    Single reader for the wedge and watch-wait evidence helpers so both
    consumers share one freshness gate: unique newest scan by ``created``,
    timezone-aware, no more than 30 minutes old. Shares the existing parse
    nodes (no new parsing sites) with ``_wedge_evidence``.
    """
    try:
        scans = []
        for path in (home / "discovery").glob("scan-*.json"):
            scan = json.loads(path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(str(scan["created"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                return None, "scan timestamp has no timezone"
            scans.append((created, scan))
        if not scans:
            return None, "no discovery scan"
        newest = max(created for created, _ in scans)
        latest = [scan for created, scan in scans if created == newest]
        if len(latest) != 1:
            return None, "newest scan timestamp is ambiguous"
        scan = latest[0]
        age_seconds = (datetime.now(timezone.utc) - newest).total_seconds()
    except (OSError, ValueError, KeyError, TypeError):
        return None, "discovery scan unreadable or malformed"
    if age_seconds < 0:
        return None, "newest scan is future-dated"
    if age_seconds > 30 * 60:
        return None, "newest scan is stale"
    observations = scan.get("observations")
    if not isinstance(observations, list):
        return None, "scan observations malformed"
    return observations, None


def _wedge_evidence(home: Path) -> tuple[list[dict], str | None]:
    """Validate the whole aggregate before either consumer binds live PIDs.

    Availability does not establish per-role coverage or useful progress.
    """
    observations, unavailable = _newest_scan_observations(home)
    if unavailable is not None:
        return [], unavailable
    readings = [row for row in observations
                if isinstance(row, dict) and row.get("id") == "sense.mind.wedge-suspect"]
    if len(readings) != 1:
        return [], "wedge reading missing or ambiguous"
    observation = readings[0]
    if observation.get("state") != "verified":
        return [], "wedge reading not verified"
    reported = observation.get("suspects")
    if not isinstance(reported, list) or any(not isinstance(row, dict) for row in reported):
        return [], "wedge suspects malformed"
    for suspect in reported:
        window, pid = suspect.get("window"), suspect.get("pid")
        if not isinstance(window, str) or not isinstance(pid, int) or isinstance(pid, bool):
            return [], "wedge suspect identity malformed"
    return reported, None


def _watch_wait_evidence(home: Path) -> tuple[list[dict], str | None]:
    """Validate the watch-wait aggregate before the witness binds live PIDs.

    Same newest-scan gate as ``_wedge_evidence``, then exactly one
    ``sense.mind.watch-wait`` reading. A non-verified reading (unknown when
    no pane is capturable, or the producer predates the sense) is unavailable
    evidence, never a clean bill. Each pane verdict carries its tmux session,
    window and pid; malformed identities stay visible as an unavailable reason
    rather than silently dropping panes.
    """
    observations, unavailable = _newest_scan_observations(home)
    if unavailable is not None:
        return [], unavailable
    readings = [row for row in observations
                if isinstance(row, dict) and row.get("id") == "sense.mind.watch-wait"]
    if len(readings) != 1:
        return [], "watch-wait reading missing or ambiguous"
    observation = readings[0]
    if observation.get("state") != "verified":
        return [], "watch-wait reading not verified"
    reported = observation.get("panes")
    if not isinstance(reported, list) or any(not isinstance(row, dict) for row in reported):
        return [], "watch-wait panes malformed"
    for pane in reported:
        window, pid = pane.get("window"), pane.get("pid")
        if not isinstance(window, str) or not isinstance(pid, int) or isinstance(pid, bool):
            return [], "watch-wait pane identity malformed"
    return reported, None

def _treadmill_evidence(home: Path) -> tuple[dict[str, dict], str | None]:
    """Per-role coverage from the newest treadmill reading, or unavailable.

    Same newest-scan gate as ``_wedge_evidence``: exactly one verified
    ``sense.mind.treadmill`` reading, its roles carrying the coverage fields
    the producer stamps (wall update, last settled yield, idle, pending).
    Availability is the reading's, not proof of useful progress — the
    consumer renders what each role last did and lets the human judge.
    """
    observations, unavailable = _newest_scan_observations(home)
    if unavailable is not None:
        return {}, unavailable
    readings = [row for row in observations
                if isinstance(row, dict) and row.get("id") == "sense.mind.treadmill"]
    if len(readings) != 1:
        return {}, "treadmill reading missing or ambiguous"
    observation = readings[0]
    if observation.get("state") != "verified":
        return {}, "treadmill reading not verified"
    roles = observation.get("roles")
    if not isinstance(roles, dict) or any(not isinstance(row, dict) for row in roles.values()):
        return {}, "treadmill roles malformed"
    for row in roles.values():
        wall_updated = row.get("wall_updated")
        if wall_updated is not None and not isinstance(wall_updated, str):
            return {}, "treadmill wall_updated malformed"
        for field in ("last_yield", "idle_s", "pending"):
            value = row.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                return {}, f"treadmill {field} malformed"
    return roles, None


def _fresh_wedge_suspect(home: Path, role: str, pid: str) -> bool:
    """Return true only for valid fresh wedge evidence bound to this live pane.

    A ``provider-error`` suspect is not wedge evidence for recovery. The witness
    already holds it rather than flagging a wedge, because the same reading can
    name a pane that is still working: an omp session log carries its title
    generator's and subagents' provider errors too, so a failure on another
    agent's model (e.g. the title generator's) appears beside a healthy mind's
    turn. Respawn is destructive, so it requires an open retry chain, which
    cannot end by itself.
    """
    suspects, unavailable = _wedge_evidence(home)
    return unavailable is None and any(
        suspect["window"] == role and str(suspect["pid"]) == pid
        and suspect.get("rule") != "provider-error" for suspect in suspects)


def _mind_pane_wedged(home: Path, session: str, role: str) -> bool:
    target = f"{session}:{role}.1"
    socket = _socket_for_home(home)
    probe = _tmux("display-message", "-p", "-t", target, "#{pane_dead} #{pane_pid}", check=False, socket=socket)
    if probe.returncode:
        return False
    fields = probe.stdout.decode(errors="replace").strip().split()
    return (len(fields) == 2 and fields[0] == "0"
            and _fresh_wedge_suspect(home, role, fields[1]))


RENEWAL_SLUGS = frozenset({"discover", "senses"})
"""Resident channels whose panes depend on discovery scan freshness."""


OBS_RE = re.compile(r"seed observation ([a-z0-9-]+)(?: sha256=([0-9a-f]{64}))?\Z")
from .chat_protocol import decode_lifecycle
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


_OPENCODE_SPNR = tuple("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


def _opencode_idle(pane: str) -> bool:
    """True only at OpenCode's idle input box.

    Observed on opencode 2.0.12: an empty session shows the `Ask anything`
    placeholder, but a session with history draws the same empty input box
    without it, so the placeholder alone misses every pane after its first
    turn. Idle is the input footer with the whole input box above it empty
    (or showing only the placeholder), no `esc interrupt` status, and no
    spinner line. A typed draft, a dialog, or a busy turn holds delivery
    rather than risking a paste into the wrong surface.
    """
    lines = pane.splitlines()
    if any("esc interrupt" in line for line in lines):
        return False
    if any(line.lstrip().startswith(_OPENCODE_SPNR) for line in lines):
        return False
    footer = next((index for index in range(len(lines) - 1, -1, -1)
                   if "OpenCode Zen" in lines[index]), None)
    if footer is None:
        return False
    index = footer - 1
    while index >= 0 and lines[index].strip().startswith(("│", "┃", "|")):
        typed = lines[index].strip().lstrip("│┃| ").strip()
        if typed and not typed.startswith("Ask anything"):
            return False
        index -= 1
    return index != footer - 1
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
    unresolved_observation = None
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
                digest = match.group(2)
                unresolved_observation = None if digest else entry
                last_observation = entry.sequence
        else:
            control = decode_lifecycle(entry)
            if control is None or control.role != slug:
                continue
            if control.kind == "wake":
                pending = entry.sequence
                last_woken_observation = control.observation
                last_wake_at = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
            elif control.kind == "yield" and pending == control.wake:
                last_yield = pending
                continue_yield = pending if control.continue_work else None
                pending = None
            elif control.kind == "clear":
                last_clear = control.wake
    # Only the final observation's digest is returned; resolve its immutable
    # record once instead of reading one record per historical observation.
    if digest is None and unresolved_observation is not None:
        digest = record_payload(unresolved_observation)["digest"]
    return digest, pending, last_yield, last_clear, last_observation, last_woken_observation, last_wake_at, continue_yield




def clear_stalls(entries, *, now: datetime | None = None, timeout: float = 120):
    """Find settled wakes whose idle clear has not happened within its budget."""
    now = now or datetime.now(timezone.utc)
    pending = {}
    settled = {}
    for entry in entries:
        if entry.source != "seed":
            continue
        control = decode_lifecycle(entry)
        if control is None:
            continue
        role = control.role
        if control.kind == "wake":
            pending[role] = entry.sequence
            settled.pop(role, None)
        elif control.kind == "yield" and pending.get(role) == control.wake:
            pending.pop(role)
            settled[role] = entry
        elif control.kind == "clear":
            receipt = settled.get(role)
            if receipt and decode_lifecycle(receipt).wake == control.wake:
                settled.pop(role)
    return {slug: entry for slug, entry in settled.items()
            if (now - datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))).total_seconds() >= timeout}




def _observation_text(slug: str, frame: str) -> str:
    if slug not in {"discover", "senses"}:
        return frame
    # "DRIFT " joins "UNKNOWN " for the senses pane: both are unverified states,
    # so both samples must reach the observation text that the wake carries.
    prefixes = ("STATE:", "UNKNOWN ", "UNAVAILABLE command.", "AVAILABLE command.",
                "PERMISSION REQUESTS:", "REQUEST ") if slug == "discover" else (
                    "STATE:", "UNKNOWN ", "DRIFT ")
    # A rendered display age grows while meaning is unchanged; redact it here.
    # State classes and STATE remain in the digest; a Redmi cursor does not.
    # A rendered Redmi cursor is source progress, not a changed observation.
    lines = (redact_redmi_cursor(redact_display_age(line), slug=slug)
             for line in frame.splitlines() if line.startswith(prefixes))
    return "\n".join(lines)

def stale_pends(entries, *, now: datetime | None = None, timeout: float = 600):
    """Find roles whose pending wake has not yielded within the timeout."""
    now = now or datetime.now(timezone.utc)
    pending: dict[str, int] = {}
    entries_by_slug: dict[str, FeedEntry] = {}
    for entry in entries:
        if entry.source != "seed":
            continue
        control = decode_lifecycle(entry)
        if control is None:
            continue
        role = control.role
        if control.kind == "wake":
            pending[role] = entry.sequence
            entries_by_slug[role] = entry
        elif control.kind == "yield" and pending.get(role) == control.wake:
            pending.pop(role)
            entries_by_slug.pop(role, None)
    return {slug: entries_by_slug[slug] for slug in pending
            if (now - datetime.fromisoformat(entries_by_slug[slug].timestamp.replace("Z", "+00:00"))).total_seconds() >= timeout}


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
    if engine not in {"omp", "codex", "opencode"}:
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
    if engine == "opencode":
        return _opencode_idle("\n".join(lines))
    return _omp_idle_mode("\n".join(lines)) is not None




def _restore_text(home: Path, slug: str, session: str, *, wake_delivery: bool = False) -> str:
    from . import wall
    return wall.restore(home, slug, session)


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
        # An absolute engine keeps the mind runnable under a PATH that no longer
        # names it; the write happens once, so a bare name is never healed later.
        argv[0] = shutil.which(argv[0]) or argv[0]
        mind.write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n", encoding="utf-8")
        mind.chmod(0o755)
    top = home / "top-pains" / slug
    if not top.exists():
        top.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                       " -m mishe_tauftauf.wall_view --home " + shlex.quote(str(home.resolve())) +
                       " --role " + shlex.quote(slug) + "\n", encoding="utf-8")
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


def loop(home: Path, slug: str, **selection) -> str:
    """Inspect/select a single desired launcher without touching its live pane."""
    from .mind_loop import select
    return select(home, slug, **selection)


def _mind_launch_argv(home: Path, slug: str) -> tuple[str, ...]:
    from .runtime_source import pane_python_path
    # The mind pane follows the home's pin like every other pane of this home, so
    # a supervisor raised from a scratch tree keeps the resident mind on the
    # declared byte-set instead of handing it the tree that respawned it.
    python_path = pane_python_path(home, Path(__file__).resolve().parents[2])
    site_bin = str(home / "bin")
    inherited_path = os.environ.get("PATH", "/usr/bin:/bin")
    path_tail = [entry for entry in inherited_path.split(os.pathsep) if entry != site_bin]
    mind_path = os.pathsep.join((site_bin, *path_tail))
    # A restored legacy launcher must not inherit a calling resident's identity.
    return ("-c", str(home.parent.resolve()), "env", "-u", "MISHE_LOOP_RECEIPT", f"PATH={mind_path}",
            f"PYTHONPATH={python_path}",
            f"XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}",
            f"MISHE_SEED_HOME={home.resolve()}",
            str(home / "minds" / slug))


_MODEL_FLAG_RE = re.compile(r"(?:^|\s)--model[ \t]+(\S+)")


def _launcher_model(path: Path) -> str | None:
    """Read the model a mind launcher selects, without running it.

    Model identity is otherwise observable only from ``/proc/<pid>/cmdline``; the
    launcher bytes are the durable record of which model an invocation runs on.
    Shell comments are skipped so a decoy ``--model`` in a comment cannot mask it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _MODEL_FLAG_RE.search(line)
        if match:
            return match.group(1)
    return None


def _record_mind_model(home: Path, slug: str, launcher: Path, reason: str) -> None:
    """Record the model a respawned mind runs on.

    Emitted only after a respawn, and read from the launcher the new process
    actually runs, so the record can never attribute a stale launcher to a
    running mind. Absence is recorded too: a launcher losing ``--model`` is a
    model change, not silence. ``append_runtime_once`` keeps one marker per
    (slug, launcher, reason, model), so an idle rotation that lands on the same
    model does not add a line per clear.
    """
    from .feed import Feed
    chosen = _launcher_model(launcher)
    marker = (f"mind model top-pain {slug} launcher={launcher} reason={reason} "
               f"model={chosen if chosen is not None else 'none'}")
    Feed(home).append_runtime_once("mishe-tauftauf", marker)


def _await_session(session: str, timeout: float = 15.0, socket: str | None = None) -> bool:
    """Wait for a peer supervisor's concurrent session raise to become visible."""
    deadline = time.monotonic() + timeout
    while _tmux("has-session", "-t", session, check=False, socket=socket).returncode != 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _new_session(home: Path, session: str, first_window: str, workspace: str) -> None:
    """Raise outside seed cgroups and wait for the actual session, not a unit.

    The socket is always the per-site socket derived from the home: an
    inherited TMUX_TMPDIR (a drop-in, a foreign shell) must not redirect the
    raise to another site's socket, where it would fork a duplicate session
    on a foreign server. The socket directory is created explicitly because
    tmux 3.4 falls back to the default socket SILENTLY when $TMUX_TMPDIR
    cannot be created — fail loudly instead of forking elsewhere.
    """
    from .plant import _site_tmux_tmpdir
    from .tmux import TmuxError
    tmux_tmpdir = str(_site_tmux_tmpdir(home))
    socket = str(Path(tmux_tmpdir) / f"tmux-{os.getuid()}" / "default")
    try:
        Path(tmux_tmpdir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TmuxError(f"UNKNOWN tmux socket dir {tmux_tmpdir} cannot be created: {exc}") from exc
    argv = ["new-session", "-d", "-s", session, "-n", first_window, "-c", workspace, "sh"]
    runner = os.environ.get("MISHE_SESSION_RUNNER")
    if runner is None:
        runner = shutil.which("systemd-run") or ""
    if runner and os.environ.get("XDG_RUNTIME_DIR"):
        if Path(runner).name == "systemd-run":
            from .session_runner import raise_session
            try:
                raise_session(session, first_window, workspace,
                              Path(os.environ["XDG_RUNTIME_DIR"]) / "mishe-session-raises",
                              systemd_runner=runner, tmux_tmpdir=tmux_tmpdir)
            except RuntimeError as exc:
                raise TmuxError(str(exc)) from exc
        else:
            # Keep the literal legacy argv for installed adapters, with the
            # per-site socket pinned the same way. No shell command string
            # needs to be interpreted by the receiving boundary.
            raised = [runner, "--user", "--scope", "--collect", "--unit",
                      f"{session}-session.scope", "--description",
                      f"Mishe resident session {session}", "env", "-u", "TMUX",
                      "TMUX_TMPDIR=" + tmux_tmpdir, "tmux", *argv]
            try:
                subprocess.run(raised, check=True)
            except subprocess.CalledProcessError:
                if not _await_session(session, socket=socket):
                    raise
            if not _await_session(session, socket=socket):
                raise TmuxError(f"UNKNOWN session {session!r} unavailable after startup deadline")
        return
    _tmux(*argv, socket=socket)


def _ensure_contract_windows(home: Path, session: str, interval: float) -> None:
    """Recreate health-declared utility windows after the tmux session is raised."""
    from .proc_table import window_names
    from .window_manifest import read

    names = read(home)
    if not names:
        return

    workspace = str(home.parent.resolve())
    if "ledger" in names:
        current = window_names(_tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout)
        if "ledger" not in current:
            target = f"{session}:ledger"
            _tmux("new-window", "-d", "-t", session, "-n", "ledger", "-c", workspace, "sh")
            _tmux("set-option", "-w", "-t", target, "automatic-rename", "off")
            _tmux("set-option", "-p", "-t", f"{target}.0", "remain-on-exit", "on")
            _tmux("respawn-pane", "-k", "-t", f"{target}.0",
                  "env", f"MISHE_SEED_SESSION={session}",
                  *_python_command(home, "--home", str(home), "pain", "watch", "ledger",
                                  "--interval", str(interval)))

    if "tail" in names:
        current = window_names(_tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout)
        if "tail" not in current:
            target = f"{session}:tail"
            _tmux("new-window", "-d", "-t", session, "-n", "tail", "-c", workspace, "sh")
            _tmux("set-option", "-w", "-t", target, "automatic-rename", "off")
            _tmux("set-option", "-p", "-t", f"{target}.0", "remain-on-exit", "on")
            _tmux("respawn-pane", "-k", "-t", f"{target}.0", "tail", "-f", str(home / "chat.log"))
            _tmux("split-window", "-v", "-t", target, "-c", workspace, "sh")
            _tmux("set-option", "-p", "-t", f"{target}.1", "remain-on-exit", "on")
            _tmux("select-layout", "-t", target, "even-vertical")


def start(home: Path, session: str, slug: str, interval: float) -> str:
    from .tmux import TmuxError

    slug = validate_slug(slug)
    workspace = str(home.parent.resolve())
    if not executable(home / "top-pains" / slug) or not executable(home / "minds" / slug):
        raise ValueError(f"seed {slug} needs executable top-pains/{slug} and minds/{slug}")
    created = _tmux("has-session", "-t", session, check=False).returncode != 0
    if created:
        _new_session(home, session, slug, workspace)
        _tmux("set-option", "-t", session, OWNED_OPTION, str(home.resolve()))
        identity = _tmux("display-message", "-p", "-t", session, "#{session_id}").stdout.decode().strip()
        (home / ".seed-raised").write_text(f"{session} {identity}\n", encoding="utf-8")
    elif not await_owned(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    else:
        # The receipt is durable state for consumers without the environment
        # override: senses, the CLI's default-session fallback, renderer
        # probes and stop's safety guard. It is written only when the session
        # is created, so a receipt lost while the session lives would wedge
        # those readers on unknowns or the wrong literal. Re-heal it from the
        # owned session rather than let a missing file outlive the plant.
        identity = _tmux("display-message", "-p", "-t", session, "#{session_id}").stdout.decode().strip()
        expected = f"{session} {identity}\n"
        receipt = home / ".seed-raised"
        # An unreadable receipt is the corruption this heals, not a reason
        # to wedge the supervisor on the way past it.
        try:
            current = receipt.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            current = None
        if current != expected:
            receipt.write_text(expected, encoding="utf-8")
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
    # The pane's byte-set is the one this home pins; `_python_command` carries
    # it, so a supervisor running out of a scratch tree no longer hands the
    # plant's own renderer that tree's code (the 04:25Z nine-renderer drift).
    top_cmd = ("env", f"MISHE_SEED_SESSION={session}",
               *_python_command(home, "--home", str(home), "pain", "watch", slug, "--interval", str(interval)))
    top_dead = _tmux("display-message", "-p", "-t", f"{target}.0", "#{pane_dead}").stdout.decode().strip() == "1"
    if created or new_window or top_dead:
        _tmux("respawn-pane", "-k", "-t", f"{target}.0", *top_cmd)
    mind_dead = _tmux("display-message", "-p", "-t", f"{target}.1", "#{pane_dead}").stdout.decode().strip() == "1"
    mind_wedged = (not created and not new_window and not mind_dead
                   and _mind_pane_wedged(home, session, slug))
    if created or new_window or mind_dead or mind_wedged:
        before = _tmux("display-message", "-p", "-t", f"{target}.1", "#{pane_pid}").stdout.decode().strip()
        _tmux("respawn-pane", "-k", "-t", f"{target}.1", *_mind_launch_argv(home, slug))
        still_dead = _tmux("display-message", "-p", "-t", f"{target}.1", "#{pane_dead}").stdout.decode().strip() == "1"
        after = _tmux("display-message", "-p", "-t", f"{target}.1", "#{pane_pid}").stdout.decode().strip()
        if still_dead or not after or after == before:
            raise ValueError(f"mind pane {target}.1 did not respawn; reconcile before attributing a model")
        _record_mind_model(home, slug, home / "minds" / slug, "start")
        time.sleep(2.0)
        _, pending, _, _, observation, *_ = _state(home, slug)
        if pending is not None:
            # Restored pending work is a convenience, not the supervisor's
            # reason to exist. Once the session is raised, a send that cannot
            # verify the mind's idle mode (a busy or dialog pane) must HOLD,
            # exactly as the tick and clear paths do. Letting it propagate
            # crashed the unit after a successful respawn: systemd restart 15s
            # later (observed 2026-10-07T18:15:07Z, PID 1559592), discarding a
            # live session and burning a restart for a recoverable transport
            # condition.
            try:
                from .wall import deliver
                print(deliver(home, session, slug, pending, observation,
                              "Mind process restored; reconcile the actual pending wake before acting."),
                      flush=True)
            except (OSError, ValueError, TmuxError) as exc:
                print(f"HOLD seed {slug} restore: {exc}", flush=True)
    # The utility windows are a repair, not the supervisor's reason to exist:
    # the mind's own window is already raised at this point. A site whose chat
    # log is missing (wiped or half-initialized) or whose checks directory
    # cannot be locked must HOLD like the restore path does, since raising out
    # of start is the 2026-10-07T18:15:07Z restart-loop class.
    try:
        with _lock(home):
            _ensure_contract_windows(home, session, interval)
    except (OSError, ValueError, TmuxError) as exc:
        print(f"HOLD seed {slug} contract windows: {exc}", flush=True)
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
    from .tmux import TmuxError
    try:
        return wall.tick(home, session, slug, self_pick_seconds)
    except (OSError, ValueError, TmuxError) as exc:
        return f"UNKNOWN seed {slug} transport/observation: {exc}"




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




def yield_wake(home: Path, slug: str, wake: int, handoff_file: Path, continue_task: bool = False,
               result: str = "unspecified", source_is_anchor: bool = False) -> str:
    handoff_source = Path(handoff_file).resolve()
    handoff_text = handoff_source.read_text(encoding="utf-8")
    if not handoff_text.strip():
        raise ValueError("handoff is empty")
    if result not in {"changed", "verified", "blocked", "unspecified"}:
        raise ValueError("result must be changed, verified, or blocked")
    from . import wall
    return wall.settle(home, slug, wake, handoff_text, continue_task, result,
                       source_path=handoff_source, source_is_anchor=source_is_anchor)


def clear(home: Path, session: str, slug: str) -> str:
    from . import wall
    return wall.clear(home, session, validate_slug(slug))


def status(home: Path, slug: str) -> str:
    digest, pending, last_yield, last_clear, _, _, _, continue_yield = _state(home, validate_slug(slug))
    return (f"seed {slug}: observation={digest or 'none'} pending={pending or 'none'} "
            f"yield={last_yield or 'none'} clear={last_clear or 'none'} "
            f"continue={continue_yield or 'none'}")


def follow(home: Path, session: str, slug: str, interval: float, self_pick_seconds: float = 3600) -> None:
    if interval <= 0:
        raise ValueError("interval must be positive")
    last_status = None
    while True:
        status = tick(home, session, slug, self_pick_seconds)
        if status and status != last_status:
            print(status, flush=True)
            last_status = status
        time.sleep(interval)


def _clear_due(home: Path, slug: str, grace_seconds: float) -> bool:
    _, pending, last_yield, last_clear, _, _, _, _ = _state(home, slug)
    if pending is not None or last_yield is None or last_yield == last_clear:
        return False
    for entry in reversed(Feed(home).entries()):
        control = decode_lifecycle(entry)
        if control and control.kind == "yield" and control.role == slug and control.wake == last_yield:
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
    last_status = None
    while True:
        start(home, session, slug, interval)
        # A semantic checker outage (for example an unavailable model) is a
        # HOLD, not a fatal error. Let the resident supervisor keep tending
        # instead of exiting and letting systemd restart it in a tight loop.
        try:
            status = tick(home, session, slug, self_pick_seconds)
            if status and status != last_status:
                print(status, flush=True)
                last_status = status
        except ValueError as exc:
            print(f"HOLD seed {slug} tick: {exc}", flush=True)
            last_status = None
        if _clear_due(home, slug, clear_grace_seconds) and _mind_idle(session, slug):
            try:
                print(clear(home, session, slug), flush=True)
            except ValueError as exc:
                print(f"HOLD seed {slug} clear: {exc}", flush=True)
        time.sleep(interval)
