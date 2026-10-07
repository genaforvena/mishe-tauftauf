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
from .observations import executable, strip_owned_chrome, validate_home, validate_slug
from .tmux import OWNED_OPTION, _pane_stopped_or_dead, _python_command, _tmux, await_owned, capture_raw, lease_value, owns_session


def _wedge_evidence(home: Path) -> tuple[list[dict], str | None]:
    """Validate the whole aggregate before either consumer binds live PIDs.

    Availability does not establish per-role coverage or useful progress.
    """
    try:
        scans = []
        for path in (home / "discovery").glob("scan-*.json"):
            scan = json.loads(path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(str(scan["created"]).replace("Z", "+00:00"))
            if created.tzinfo is None:
                return [], "scan timestamp has no timezone"
            scans.append((created, scan))
        if not scans:
            return [], "no discovery scan"
        newest = max(created for created, _ in scans)
        latest = [scan for created, scan in scans if created == newest]
        if len(latest) != 1:
            return [], "newest scan timestamp is ambiguous"
        scan = latest[0]
        age_seconds = (datetime.now(timezone.utc) - newest).total_seconds()
    except (OSError, ValueError, KeyError, TypeError):
        return [], "discovery scan unreadable or malformed"
    if age_seconds < 0:
        return [], "newest scan is future-dated"
    if age_seconds > 30 * 60:
        return [], "newest scan is stale"
    observations = scan.get("observations")
    if not isinstance(observations, list):
        return [], "scan observations malformed"
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


def _fresh_wedge_suspect(home: Path, role: str, pid: str) -> bool:
    """Return true only for valid fresh evidence bound to this live pane."""
    suspects, unavailable = _wedge_evidence(home)
    return unavailable is None and any(
        suspect["window"] == role and str(suspect["pid"]) == pid for suspect in suspects)


def _mind_pane_wedged(home: Path, session: str, role: str) -> bool:
    target = f"{session}:{role}.1"
    probe = _tmux("display-message", "-p", "-t", target, "#{pane_dead} #{pane_pid}",
                  check=False)
    if probe.returncode:
        return False
    fields = probe.stdout.decode(errors="replace").strip().split()
    return (len(fields) == 2 and fields[0] == "0"
            and _fresh_wedge_suspect(home, role, fields[1]))


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
    if slug not in {"discover", "senses"}:
        return frame
    # "DRIFT " joins "UNKNOWN " for the senses pane: both are unverified states,
    # so both samples must reach the observation text that the wake carries.
    prefixes = ("STATE:", "UNKNOWN ", "UNAVAILABLE command.", "AVAILABLE command.",
                "PERMISSION REQUESTS:", "REQUEST ") if slug == "discover" else (
                    "STATE:", "UNKNOWN ", "DRIFT ")
    return "\n".join(line for line in frame.splitlines() if line.startswith(prefixes))

def stale_pends(entries, *, now: datetime | None = None, timeout: float = 600):
    """Find roles whose pending wake has not yielded within the timeout."""
    now = now or datetime.now(timezone.utc)
    pending: dict[str, int] = {}
    entries_by_slug: dict[str, FeedEntry] = {}
    for entry in entries:
        if entry.source != "seed":
            continue
        line = _receipt_line(entry.body)
        if match := WAKE_RE.fullmatch(line):
            pending[match[1]] = entry.sequence
            entries_by_slug[match[1]] = entry
        elif match := YIELD_RE.fullmatch(line):
            if pending.get(match[1]) == int(match[2]):
                pending.pop(match[1])
                entries_by_slug.pop(match[1], None)
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


def _mind_launch_argv(home: Path, slug: str) -> tuple[str, ...]:
    from .runtime_source import python_search_path
    package_root = str(Path(__file__).resolve().parents[1])
    python_path = python_search_path(package_root, os.environ.get("PYTHONPATH", ""))
    site_bin = str(home / "bin")
    inherited_path = os.environ.get("PATH", "/usr/bin:/bin")
    path_tail = [entry for entry in inherited_path.split(os.pathsep) if entry != site_bin]
    mind_path = os.pathsep.join((site_bin, *path_tail))
    return ("-c", str(home.parent.resolve()), "env", f"PATH={mind_path}",
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


def _await_session(session: str, timeout: float = 15.0) -> bool:
    """Wait for a peer supervisor's concurrent session raise to become visible."""
    deadline = time.monotonic() + timeout
    while _tmux("has-session", "-t", session, check=False).returncode != 0:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _new_session(session: str, first_window: str, workspace: str) -> None:
    """Create the tmux server outside any seed service cgroup.

    A seed supervisor creates the server, so it inherits that unit's cgroup; a
    later activation restarting the seed set cgroup-kills the server and every
    mind turn with it. Raising the server in its own transient user scope puts
    it beside the seed services instead of inside one, so a restart of any seed
    unit leaves the session alive. systemd is optional: where it is missing the
    plain tmux call still works, and the cgroup hazard does not arise there.
    """
    argv = ["new-session", "-d", "-s", session, "-n", first_window, "-c", workspace, "sh"]
    runner = os.environ.get("MISHE_SESSION_RUNNER")
    if runner is None:
        runner = shutil.which("systemd-run") or ""
    if runner and os.environ.get("XDG_RUNTIME_DIR"):
        scoped = [runner, "--user", "--scope", "--collect",
                  "--unit", f"{session}-session.scope",
                  "--description", f"Mishe resident session {session}",
                  "tmux", *argv]
        try:
            subprocess.run(scoped, check=True)
        except subprocess.CalledProcessError:
            # Every supervisor for this session races the same fixed scope unit
            # name, so a peer that wins makes the others fail here with "unit was
            # already loaded". Attach to the peer's session instead of exiting,
            # which would crash-loop the supervisor and turn a server restart
            # into a plant-wide RED.
            if not _await_session(session):
                raise
        return
    _tmux(*argv)

def start(home: Path, session: str, slug: str, interval: float) -> str:
    from .tmux import TmuxError

    slug = validate_slug(slug)
    workspace = str(home.parent.resolve())
    if not executable(home / "top-pains" / slug) or not executable(home / "minds" / slug):
        raise ValueError(f"seed {slug} needs executable top-pains/{slug} and minds/{slug}")
    created = _tmux("has-session", "-t", session, check=False).returncode != 0
    if created:
        _new_session(session, slug, workspace)
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
    from .runtime_source import python_search_path
    package_root = str(Path(__file__).resolve().parents[1])
    python_path = python_search_path(package_root, os.environ.get("PYTHONPATH", ""))
    top_cmd = ("env", f"MISHE_SEED_SESSION={session}", f"PYTHONPATH={python_path}",
               *_python_command("--home", str(home), "pain", "watch", slug, "--interval", str(interval)))
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
        if _state(home, slug)[1] is not None:
            # Restored pending work is a convenience, not the supervisor's
            # reason to exist. Once the session is raised, a send that cannot
            # verify the mind's idle mode (a busy or dialog pane) must HOLD,
            # exactly as the tick and clear paths do. Letting it propagate
            # crashed the unit after a successful respawn: systemd restart 15s
            # later (observed 2026-10-07T18:15:07Z, PID 1559592), discarding a
            # live session and burning a restart for a recoverable transport
            # condition.
            try:
                _send(f"{target}.1", _restore_text(home, slug, session))
            except (OSError, ValueError, TmuxError) as exc:
                print(f"HOLD seed {slug} restore: {exc}", flush=True)
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
               result: str = "unspecified") -> str:
    handoff_text = Path(handoff_file).resolve().read_text(encoding="utf-8")
    if not handoff_text.strip():
        raise ValueError("handoff is empty")
    if result not in {"changed", "verified", "blocked", "unspecified"}:
        raise ValueError("result must be changed, verified, or blocked")
    from . import wall
    return wall.settle(home, slug, wake, handoff_text, continue_task, result)


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
