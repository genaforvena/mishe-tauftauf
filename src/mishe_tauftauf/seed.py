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

from .feed import Feed
from . import discovery, task_state
from .observations import executable, strip_owned_chrome, validate_home, validate_slug
from .tmux import OWNED_OPTION, _pane_stopped_or_dead, _python_command, _tmux, capture_raw, lease_value, owns_session


RENEWAL_SLUGS = frozenset({"discover", "senses"})
"""Resident channels whose panes depend on discovery scan freshness."""


OBS_RE = re.compile(r"seed observation ([a-z0-9-]+) sha256=([0-9a-f]{64})\Z")
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


def _state(home: Path, slug: str) -> tuple[str | None, int | None, int | None, int | None, int | None, int | None, datetime | None, int | None]:
    digest = None
    last_observation = None
    last_woken_observation = None
    last_wake_at = None
    pending = None
    last_yield = None
    last_clear = None
    continue_yield = None
    for entry in Feed(home).entries():
        if entry.source != "seed":
            continue
        line = _receipt_line(entry.body)
        if match := OBS_RE.fullmatch(line):
            if match.group(1) == slug:
                digest = match.group(2)
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


def _observation_text(slug: str, frame: str) -> str:
    if slug == "witness":
        # Its pane includes the latest chat text and rate counts. Hashing that
        # text makes witness observe its own observation receipt forever.
        prefixes = ("WINDOWS:", "CI:", "OPEN TASKS:", "LOOP:", "STATE:")
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
    for entry in Feed(home).entries():
        if entry.body.startswith(f"seed wake {slug} ") and entry.sequence == pending:
            last_delivery = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        elif _receipt_line(entry.body) == f"seed redeliver {slug} wake={pending}":
            last_delivery = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
    if last_delivery is None:
        return f"UNKNOWN seed {slug} wake {pending} has no delivery record"
    if (datetime.now(timezone.utc) - last_delivery).total_seconds() < 60:
        return f"held seed {slug} wake {pending} unsettled"
    if not _mind_ready(session, slug):
        return f"held seed {slug} wake {pending} mind busy"
    entries = Feed(home).entries()
    original = next(entry for entry in entries if entry.sequence == pending)
    wake_match = WAKE_RE.fullmatch(_receipt_line(original.body))
    if wake_match and wake_match.group(4):
        plan = task_state.states(entries).get(wake_match.group(4))
        if plan is not None and plan.attempt_wake != pending:
            task_state.record_attempt(home, plan, pending, int(wake_match.group(2)), owner=slug)
    _send(f"{session}:{slug}.1", _restore_text(home, slug, session, wake_delivery=True) + "\n" + (
        f"REDELIVERY of unsettled WAKE {pending} for {slug}. A prior delivery may have been lost during clear. "
        "First inspect the live top pane, chat.log, and artifacts for this exact wake. "
        "Reconcile any prior effect before making another change. Then complete one bounded step, "
        f"write a handoff, and settle with seed yield --slug {slug} --wake {pending} --file HANDOFF_FILE "
        "--result changed|verified|blocked.\n") + "ORIGINAL OBLIGATION\n" + original.body + "\n")
    Feed(home).append("seed", f"seed redeliver {slug} wake={pending}\n"
                      "The prior wake remains unsettled. The mind was idle, so the supervisor sent it again; "
                      "check earlier effects before making another change.")
    return f"redelivered seed {slug} wake {pending}"


def _restore_text(home: Path, slug: str, session: str, *, wake_delivery: bool = False) -> str:
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
            "TASK STATE\n" + "\n".join(task_state.lines(Feed(home).entries(), slug)) + "\n"
            f"{next_action}\n"
            f"The canonical site is {home.resolve()}. The CLI resolves it automatically from MISHE_SEED_HOME, "
            "which fresh mind launches set to this path; if this older process lacks it, use this exact path "
            "in shell commands. Never retype the directory from memory.\n"
            f"The installed runtime source is {runtime}; the development checkout is {home.parent.resolve()}. "
            "Inspect the source that actually runs and the task's isolated candidate; development dirt is not deployed code. "
            f"Use the canonical CLI {home.resolve() / 'bin/mishe-tauftauf'} when present.\n"
            f"Read the live top pane with mishe-tauftauf --home {shlex.quote(str(home))} pain read {slug} --launcher tmux --session {shlex.quote(session)}. "
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
            "if [ \"$rc\" -eq 0 ]; then printf '%s\\n' 'STATE: GREEN · NEXT: verify and land one change'; "
            "else printf '%s\\n' 'STATE: RED · NEXT: repair the failed check'; fi\n"
            "printf '%s\\n' \"$debt\" | head -n 2\n",
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
    from .runtime_source import package_for
    package_root = str(package_for(home, Path(__file__).resolve().parents[1]))
    python_path = os.pathsep.join(part for part in (package_root, os.environ.get("PYTHONPATH", "")) if part)
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
    from .runtime_source import package_for
    package_root = str(package_for(home, Path(__file__).resolve().parents[1]))
    python_path = os.pathsep.join(part for part in (package_root, os.environ.get("PYTHONPATH", "")) if part)
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
        frame = strip_owned_chrome(full).strip()
        if not frame:
            return f"UNKNOWN seed {slug} top pane empty"
        digest = hashlib.sha256(_observation_text(slug, frame).encode("utf-8")).hexdigest()
        previous, pending, last_yield, last_clear, last_observation, last_woken_observation, last_wake_at, continue_yield = _state(home, slug)
        changed = digest != previous
        if changed:
            state_line = next((line for line in frame.splitlines() if line.startswith("STATE:")), "state not stated")
            observation = Feed(home).append("seed", f"seed observation {slug} sha256={digest}\n"
                                       f"The {slug} top pane's meaningful state changed: {state_line}. "
                                       "Read the live pane for the full evidence and next action.")
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
        plans = [plan for plan in task_state.states(entries).values() if plan.owner == slug]
        selected = task_state.select_task(entries, slug)
        opportunity = None
        if plans and selected is None:
            opportunity = task_state.independent_opportunity(entries, slug) if self_pick_due else None
            self_pick_due = bool(opportunity)
            continue_due = False
            if last_observation == last_woken_observation and external is None and opportunity is None:
                return f"waiting seed {slug} task prerequisites unchanged"
        if last_observation is None or (last_observation == last_woken_observation and not self_pick_due and external is None and not continue_due and selected is None):
            return f"quiet seed {slug} unchanged"
        mind_dead = _tmux("display-message", "-p", "-t", f"{session}:{slug}.1", "#{pane_dead}", check=False)
        if mind_dead.returncode or mind_dead.stdout.decode().strip() == "1":
            return f"HOLD seed {slug} resident mind absent"
        if not _mind_ready(session, slug):
            return f"HOLD seed {slug} resident mind busy"
        event_suffix = f" event={external.sequence}" if external is not None else ""
        reason = (f"actionable task {selected.identity}" if selected is not None else
                  f"addressed chat event {external.sequence}" if external is not None else
                  "continued task" if continue_due else "quiet self-pick" if self_pick_due and not changed else
                  "changed top-pane observation")
        task_suffix = f" task={selected.identity}" if selected is not None else ""
        wake = Feed(home).append("seed", f"seed wake {slug} observation={last_observation}{event_suffix}{task_suffix}\n"
                                 f"The supervisor asked {slug} to take one bounded, checked step because of {reason}. "
                                 "The mind must leave a handoff and settle this exact wake." +
                                 (f"\n[task-opportunity] owner={slug} waiting={opportunity}\n"
                                  "INDEPENDENT WORK OPPORTUNITY: leave blocked acceptance waiting; "
                                  "produce an admissible child measurement, repair, analysis or limitations draft."
                                  if opportunity else ""))
        if selected is not None:
            task_state.record_attempt(home, selected, wake.sequence, last_observation, owner=slug)
        prompt = (f"WAKE {wake.sequence} for {slug}. Read your live top pane now. Current observation:\n{frame}\n"
                  + (f"TASK TO ADVANCE: {selected.identity}\nNEXT STEP: {selected.next_step}\n"
                     "Before yield, use task step with a changed outcome and checked evidence for a distinct next step, "
                     "or task wait naming the prerequisite producer and exact retry event/deadline/task. "
                     f"EVIDENCE: {selected.evidence or '(none)'} sha256={selected.evidence_sha256 or '(none)'}\n"
                     "This wake reserves the delivered task for your current attempt. Its consumed/waiting state "
                     "prevents another dispatch; it does not block this attempt. The pane's next task is for a later idle turn. "
                     "Read evidence at its exact path; site artifacts are intentionally outside tracked source. "
                     "Complete a bounded child with task finish; keep its goal open.\n"
                     if selected is not None else "")
                  + "TASK STATE (waiting tasks must not be retaken unless their retry fires):\n"
                  + "\n".join(task_state.lines(entries, slug)) + "\n"
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


def yield_wake(home: Path, slug: str, wake: int, handoff_file: Path, continue_task: bool = False,
               result: str = "unspecified") -> str:
    slug = validate_slug(slug)
    handoff_text = handoff_file.read_text(encoding="utf-8")
    if not handoff_text.strip():
        raise ValueError("handoff is empty")
    if result not in {"changed", "verified", "blocked", "unspecified"}:
        raise ValueError("result must be changed, verified, or blocked")
    with _lock(home):
        _, pending, _, _, _, observation, _, _ = _state(home, slug)
        if pending != wake:
            raise ValueError(f"wake {wake} is not the pending wake for {slug}")
        entries = Feed(home).entries()
        original = next(entry for entry in entries if entry.sequence == wake)
        wake_match = WAKE_RE.fullmatch(_receipt_line(original.body))
        selected_task = wake_match.group(4) if wake_match else None
        if continue_task and selected_task and task_state.select_task(
                [entry for entry in entries if entry.sequence != wake], slug) is None:
            raise ValueError("continuation requires a checked next step or fired retry; record task step or task wait")
        archive = home / "artifacts" / f"seed-{slug}-wake-{wake}.md"
        if archive.exists() and archive.read_text(encoding="utf-8") != handoff_text:
            raise ValueError(f"archived handoff for wake {wake} differs; reconcile before yielding")
        path = home / "handoffs" / f"{slug}.md"
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_name(f".{slug}.{os.getpid()}.tmp")
        temporary.write_text(handoff_text, encoding="utf-8")
        os.replace(temporary, path)
        archive.parent.mkdir(exist_ok=True)
        if not archive.exists():
            archived_temp = archive.with_name(f".{archive.name}.{os.getpid()}.tmp")
            archived_temp.write_text(handoff_text, encoding="utf-8")
            os.replace(archived_temp, archive)
        digest = hashlib.sha256(handoff_text.encode("utf-8")).hexdigest()
        excerpt = handoff_text.strip()[:4096]
        if len(handoff_text.strip()) > 4096:
            excerpt += "\n[truncated; read archived handoff]"
        work_prefix = f"[work] channel={slug} wake={wake} "
        header = (work_prefix + f"observation={observation or 'none'} "
                  f"result={result} continue={int(continue_task)} handoff_sha256={digest} archive={archive}")
        if selected_task:
            state_entry = next((entry for entry in reversed(entries)
                                if entry.body.startswith(f"[task-state] {selected_task}\n")), None)
            if state_entry:
                payload = json.loads(state_entry.body.splitlines()[1])
                step = hashlib.sha256((payload["next_step"] + "\0" + payload["evidence_sha256"]).encode()).hexdigest()
                header += f" task={selected_task} task_state={state_entry.sequence} task_step={step}"
                outcome = hashlib.sha256((payload.get("progress", "") + "\0" + payload.get("reason", "")).encode()).hexdigest()
                header += f" task_outcome={outcome}"
            else:
                header += f" task={selected_task}"
        prior = [entry for entry in Feed(home).entries() if entry.source == "seed" and
                 entry.body.startswith(work_prefix)]
        if prior:
            if len(prior) != 1 or prior[0].body.splitlines()[0] != header:
                raise ValueError(f"work receipt for wake {wake} differs; reconcile before yielding")
        else:
            Feed(home).append("seed", f"{header}\nHANDOFF:\n{excerpt}")
        Feed(home).append("seed", f"seed yield {slug} wake={wake}" + (" continue=1" if continue_task else "") +
                          "\nThe mind settled this wake with an archived handoff and work receipt. " +
                          ("Its task continues after context clear." if continue_task else "No continuation was requested."))
        return f"yield seed {slug} {wake}"


def clear(home: Path, session: str, slug: str) -> str:
    slug = validate_slug(slug)
    if not owns_session(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    with _lock(home):
        _, pending, last_yield, last_clear, _, _, _, _ = _state(home, slug)
        if pending is not None:
            raise ValueError(f"wake {pending} remains unsettled")
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
        old_pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip()
        _tmux("respawn-pane", "-k", "-t", target, *_mind_launch_argv(home, slug))
        time.sleep(0.5)
        new_pid = _tmux("display-message", "-p", "-t", target, "#{pane_pid}").stdout.decode().strip()
        new_dead = _tmux("display-message", "-p", "-t", target, "#{pane_dead}").stdout.decode().strip()
        if not old_pid or not new_pid or new_pid == old_pid or new_dead != "0":
            raise ValueError("mind process rotation did not produce a live new pane")
        Feed(home).append("seed", f"seed clear {slug} after={last_yield}\n"
                          "The supervisor rotated the idle mind to a fresh process in the same pane. "
                          "Its charter and latest handoff will be delivered with the next wake.")
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
        print(tick(home, session, slug, self_pick_seconds), flush=True)
        if _clear_due(home, slug, clear_grace_seconds) and _mind_idle(session, slug):
            try:
                print(clear(home, session, slug), flush=True)
            except ValueError as exc:
                print(f"HOLD seed {slug} clear: {exc}", flush=True)
        time.sleep(interval)
