"""Native-loop readiness probes and the delegation-branch detector.

A native-loop mind has no TUI, so ``seed._mind_ready`` delegates readiness to
an executable probe at ``SITE/checks/mind-ready/<role>``. The probe answers one
question: can this pane accept a complete prompt right now?

Two measured defects sit on that answer. The probe's engine test was the single
literal ``python3`` while the site's interpreters also present ``python`` (the
site venv, measured on the live permissions pane) and ``python3.12`` (a
versioned interpreter), so a live native pane whose launcher ran ``python``
read not-ready and its wake was held forever (witness DM 5822). And the hold
reported the same ``mind busy`` as a TUI mid-turn, so a starving native mind
and a busy one were indistinguishable in the journal.

``install`` keeps the canonical probe in tracked source so a replant cannot
reintroduce the narrow literal. ``detect`` reads the delegation branch — an
alive pane whose foreground command is not a TUI engine — and separates a
correct not-ready (dead pane, foreign engine, the native busy file, or no live
lifecycle) from the false negative: a gate that rejects the pane while the
role's lifecycle still shows a live mind. ``record`` persists a changed verdict
at ``SITE/checks/native-gate-<role>.json`` so the observation survives the loop
that produced it.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .chat_protocol import decode_lifecycle
from .feed import Feed, FeedError
from .observations import executable, validate_home, validate_slug
from .post_check import _save

# The engines whose TUI idle prompt seed._mind_ready recognizes itself; the
# readiness gate is consulted for every other foreground command.
TUI_ENGINES = {"omp", "codex", "opencode"}

# A lifecycle entry newer than this names a live mind; an older one names a
# mind whose last wake or yield predates any delivery the gate is holding.
LIVE_WINDOW_SECONDS = 1800.0

GATE_SOURCE = '''#!/usr/bin/env python3
"""Readiness gate for a native-loop mind pane.

The omp engine reports readiness through its TUI's idle prompt; the native
launcher has no TUI, so it marks each turn with a busy file instead and this
probe reports idle only when that file is absent while the launcher process is
alive. A pane running a different engine or a dead pane reads not-ready, so the
site's delivery path keeps holding rather than pasting into a busy mind.

The engine test accepts every name the site's python interpreter presents:
``python`` (the site venv), ``python3`` (the native launcher's env shebang)
and ``python3.12`` (a versioned interpreter). A single literal — ``python3``
alone — rejected a live native mind whose launcher ran ``python`` and held its
wake with no visible cause, because the delivery hold reads the same
``mind busy`` as a TUI mid-turn. ``src/mishe_tauftauf/native_gate.py`` keeps
this source canonical; install it with ``python -m mishe_tauftauf.native_gate
--home SITE --session SESSION --role ROLE --install``.

This probe interprets nothing from the pane text. It reads the foreground
command, the pane's dead flag and the launcher's own busy file, and exits.
"""
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT = 2
ENGINE_NAMES = ("python", "python3")
ENGINE_PREFIXES = ("python3.",)


def tmux(*args):
    return subprocess.run(("tmux", *args), capture_output=True, text=True, timeout=TIMEOUT)


def is_engine(command):
    """True for every python name this site runs, false for other engines."""
    return command in ENGINE_NAMES or command.startswith(ENGINE_PREFIXES)


def main() -> int:
    pane = os.environ.get("MISHE_SEED_PANE", "")
    role = os.environ.get("MISHE_SEED_ROLE", "")
    session = os.environ.get("MISHE_SEED_SESSION", "")
    if not pane or not role or not session:
        return 1
    owned = tmux("show-option", "-qv", "-t", session, "@mishe-tauftauf-home")
    if owned.returncode != 0 or not owned.stdout.strip():
        return 1
    home = Path(owned.stdout.strip())
    command = tmux("display-message", "-p", "-t", pane, "#{pane_current_command}")
    if command.returncode != 0 or not is_engine(command.stdout.strip()):
        return 1
    dead = tmux("display-message", "-p", "-t", pane, "#{pane_dead}")
    if dead.returncode != 0 or dead.stdout.strip() == "1":
        return 1
    if (home / "journals" / f"{role}-native.busy").exists():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# A changed verdict is a changed observation; an unchanged one is a repeated
# read of the same state and must not rewrite the check file.
_IDENTICAL_KEYS = ("branch", "engine", "gate_exit", "busy", "lifecycle", "verdict", "reason")


def install(home: Path, role: str) -> Path:
    """Write the canonical probe at ``checks/mind-ready/<role>``; return its path."""
    validate_home(home)
    validate_slug(role)
    path = home / "checks" / "mind-ready" / role
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == GATE_SOURCE:
        os.chmod(path, 0o755)
        return path
    fd, name = tempfile.mkstemp(dir=str(path.parent), prefix=".draft-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(GATE_SOURCE)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o755)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return path


def _pane_probe(session: str, role: str):
    """``(pane_dead, pane_current_command)``, or None when tmux cannot answer."""
    from . import seed

    probe = seed._tmux("display-message", "-p", "-t", f"{session}:{role}.1",
                       "#{pane_dead} #{pane_current_command}", check=False)
    if probe.returncode:
        return None
    fields = probe.stdout.decode("utf-8", "replace").split()
    if len(fields) != 2:
        return None
    return fields[0], fields[1]


def _last_lifecycle(home: Path, role: str):
    """The role's newest decoded lifecycle entry as ``(at, control)``, or None."""
    latest = None
    for entry in Feed(home).entries():
        if entry.source != "seed":
            continue
        control = decode_lifecycle(entry)
        if control is None or control.role != role:
            continue
        latest = (entry.timestamp, control)
    return latest


def detect(home: Path, session: str, role: str, *, now: datetime | None = None) -> dict:
    """Read the readiness-gate branch for ``role`` and classify its verdict.

    RED names the false negative the branch can produce: an alive non-TUI pane
    the gate rejects while the role's lifecycle still shows a live mind, with no
    busy file — a pane that could accept a prompt but is read as unable to.
    Every other not-ready is classified correctly (dead pane, mid-turn busy
    file, no live lifecycle owed work), and unreadable evidence stays UNKNOWN.
    """
    validate_home(home)
    validate_slug(role)
    now = now or datetime.now(timezone.utc)
    verdict = {"version": 1, "role": role, "at": now.isoformat(), "branch": None,
               "engine": None, "gate": None, "gate_exit": None, "busy": None,
               "lifecycle": None, "lifecycle_age": None, "verdict": None, "reason": ""}
    pane = _pane_probe(session, role)
    if pane is None:
        verdict.update(verdict="UNKNOWN", reason="pane probe unavailable")
        return verdict
    dead, command = pane
    verdict["engine"] = command
    if dead == "1":
        verdict.update(verdict="OK", reason="pane dead; a not-ready gate is correct")
        return verdict
    if command in TUI_ENGINES:
        verdict.update(branch="tui", verdict="OK",
                       reason=f"TUI engine {command}; the readiness gate is not consulted")
        return verdict
    verdict["branch"] = "delegation"
    verdict["busy"] = (home / "journals" / f"{role}-native.busy").exists()
    try:
        lifecycle = _last_lifecycle(home, role)
    except FeedError as exc:
        verdict.update(verdict="UNKNOWN", reason=f"lifecycle evidence unavailable: {exc}")
        return verdict
    if lifecycle is not None:
        at, control = lifecycle
        label = (f"wake observation={control.observation}" if control.kind == "wake"
                 else f"{control.kind} wake={control.wake}")
        verdict["lifecycle"] = f"{label} at {at}"
        try:
            verdict["lifecycle_age"] = (now - datetime.fromisoformat(
                at.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            verdict["lifecycle_age"] = None
    gate = home / "checks" / "mind-ready" / role
    if not executable(gate):
        verdict.update(verdict="RED", reason=f"delegation branch ({command}) has no readiness probe")
        return verdict
    verdict["gate"] = str(gate)
    try:
        result = subprocess.run([str(gate)], capture_output=True, text=True, timeout=5,
                                env={**os.environ, "MISHE_SEED_SESSION": session,
                                     "MISHE_SEED_ROLE": role, "MISHE_SEED_PANE": f"{session}:{role}.1"})
    except (OSError, subprocess.SubprocessError) as exc:
        verdict.update(verdict="UNKNOWN", reason=f"probe failed: {exc}")
        return verdict
    verdict["gate_exit"] = result.returncode
    age = verdict["lifecycle_age"]
    if result.returncode == 0:
        verdict.update(verdict="GREEN", reason="probe reports the pane can accept a prompt")
    elif verdict["busy"]:
        verdict.update(verdict="OK", reason="native mind mid-turn; the busy file correctly reads not-ready")
    elif lifecycle is None:
        verdict.update(verdict="OK", reason="no lifecycle evidence; the role owes no work")
    elif age is None:
        verdict.update(verdict="UNKNOWN", reason="lifecycle timestamp unreadable while the probe reads not-ready")
    elif age <= LIVE_WINDOW_SECONDS:
        verdict.update(verdict="RED",
                       reason=(f"probe rejects the {command} pane while the lifecycle shows "
                               f"{verdict['lifecycle']} ({age:.0f}s old): a live mind read not-ready"))
    else:
        verdict.update(verdict="OK",
                       reason=f"no live lifecycle ({age:.0f}s old); a not-ready pane owes no work")
    return verdict


def record(home: Path, verdict: dict):
    """Persist a changed verdict; return it, or None when nothing changed."""
    path = home / "checks" / f"native-gate-{verdict['role']}.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if isinstance(previous, dict) and all(previous.get(key) == verdict.get(key) for key in _IDENTICAL_KEYS):
        return None
    _save(path, verdict)
    return verdict


def lines(verdict: dict) -> list[str]:
    """Render a verdict for a pane or a journal line."""
    detail = (f"  Evidence: engine={verdict.get('engine')} branch={verdict.get('branch')} "
              f"gate_exit={verdict.get('gate_exit')} busy={verdict.get('busy')} "
              f"lifecycle={verdict.get('lifecycle')} age={verdict.get('lifecycle_age')}")
    return [f"NATIVE GATE: {verdict.get('verdict')} — {verdict.get('reason')}", detail]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--install", action="store_true",
                        help="write the canonical probe at checks/mind-ready/ROLE and exit")
    parser.add_argument("--record", action="store_true",
                        help="persist the verdict when it changed")
    args = parser.parse_args()
    if args.install:
        print(f"installed {install(args.home, args.role)}")
        return
    verdict = detect(args.home, args.session, args.role)
    if args.record:
        record(args.home, verdict)
    for line in lines(verdict):
        print(line)


if __name__ == "__main__":
    main()
