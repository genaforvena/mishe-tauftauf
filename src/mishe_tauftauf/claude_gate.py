#!/usr/bin/env python3
"""Run a Mishe mind on Claude Code: readiness gate, lifecycle hooks, failure check.

omp, codex and opencode report idle through their TUIs; Claude Code does not.
Its prompt box stays on screen while it generates and while Stop hooks run, so
the prompt is not a readiness signal. Claude Code's own lifecycle hooks are:
they write small state files, and the probe reads only those files, the pane's
foreground command and its dead flag. Nothing here interprets pane text.

State files, under SITE/journals/ (the file's mtime is its age):

  ROLE-claude.alive    SessionStart wrote it; SessionEnd removed it
  ROLE-claude.busy     UserPromptSubmit wrote it; Stop / StopFailure remove it
  ROLE-claude.blocked  a permission dialog is open; Stop removes it
  ROLE-claude.failure  StopFailure wrote it (the turn ended on an API error)
  ROLE-claude.hook-errors  a hook could not write state (never silent)

Every uncertain reading is NOT ready and, for ``check``, UNKNOWN or a problem:
a missing file, a stale busy file, a dialog, a dead pane or another engine all
hold delivery, and the scheduled check routes the standing ones to Health.

One file, stdlib only, so it can be installed beside a site and run from
there:  python3 claude_gate.py install --home SITE --role ROLE --model MODEL
Modes: hook EVENT | probe | check | install.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ENGINE = "claude"
STALE_BUSY = 45 * 60      # a turn this long with no Stop is a stuck or interrupted turn
STALE_BLOCKED = 120       # a permission dialog nobody answered
TMUX_TIMEOUT = 2
SUFFIXES = ("alive", "busy", "blocked", "failure", "hook-errors")


def journal(home: Path, role: str, suffix: str) -> Path:
    return home / "journals" / f"{role}-{ENGINE}.{suffix}"


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)


def _touch(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(text or f"{time.time():.0f}\n")
    os.replace(tmp, path)


def hook(event: str, home: Path, role: str) -> int:
    """Record one lifecycle event. Silent on stdout (UserPromptSubmit and
    SessionStart output would be injected into the model's context); a failure
    is written down, never swallowed."""
    try:
        if event == "SessionStart":
            for suffix in ("busy", "blocked", "failure"):
                _remove(journal(home, role, suffix))
            _touch(journal(home, role, "alive"), f"{os.getppid()}\n")
        elif event == "SessionEnd":
            for suffix in ("alive", "busy", "blocked"):
                _remove(journal(home, role, suffix))
        elif event == "UserPromptSubmit":
            _remove(journal(home, role, "failure"))
            _remove(journal(home, role, "blocked"))
            _touch(journal(home, role, "busy"))
        elif event == "Stop":
            _remove(journal(home, role, "busy"))
            _remove(journal(home, role, "blocked"))
        elif event == "StopFailure":
            _remove(journal(home, role, "busy"))
            _touch(journal(home, role, "failure"))
        elif event == "PermissionPrompt":
            _touch(journal(home, role, "blocked"))
        else:
            raise ValueError("unknown hook event " + event)
    except (OSError, ValueError) as exc:
        try:
            journal(home, role, "hook-errors").parent.mkdir(parents=True, exist_ok=True)
            with journal(home, role, "hook-errors").open("a") as handle:
                handle.write(f"{time.time():.0f} {event}: {exc}\n")
        except OSError:
            pass
    return 0


def _tmux(socket: str | None, *args: str) -> subprocess.CompletedProcess:
    argv = ["tmux"] + (["-S", socket] if socket else []) + list(args)
    return subprocess.run(argv, capture_output=True, text=True, timeout=TMUX_TIMEOUT)


def ready(home: Path, role: str, command: str | None, dead: str | None, now: float | None = None) -> tuple[bool, str]:
    """Pure readiness from the pane facts and the state files."""
    now = time.time() if now is None else now
    if command != ENGINE:
        return False, f"pane engine is {command!r}, not {ENGINE}"
    if dead != "0":
        return False, "pane is dead"
    if not journal(home, role, "alive").exists():
        return False, "no SessionStart receipt"
    for suffix in ("busy", "blocked"):
        if journal(home, role, suffix).exists():
            return False, suffix
    return True, "idle"


def probe() -> int:
    """checks/mind-ready/ROLE contract: exit 0 only at a known idle prompt."""
    pane = os.environ.get("MISHE_SEED_PANE", "")
    role = os.environ.get("MISHE_SEED_ROLE", "")
    session = os.environ.get("MISHE_SEED_SESSION", "")
    if not (pane and role and session):
        return 1
    try:
        owned = _tmux(None, "show-option", "-qv", "-t", session, "@mishe-tauftauf-home")
        if owned.returncode or not owned.stdout.strip():
            return 1
        command = _tmux(None, "display-message", "-p", "-t", pane, "#{pane_current_command}")
        dead = _tmux(None, "display-message", "-p", "-t", pane, "#{pane_dead}")
    except (OSError, subprocess.SubprocessError):
        return 1
    if command.returncode or dead.returncode:
        return 1
    return 0 if ready(Path(owned.stdout.strip()), role, command.stdout.strip(), dead.stdout.strip())[0] else 1


def claude_roles(home: Path) -> list[str]:
    """Roles whose launcher declares this engine (marker line, set by install)."""
    roles = []
    for path in sorted((home / "minds").glob("*")):
        try:
            if path.is_file() and f"# mishe-engine: {ENGINE}" in path.read_text():
                roles.append(path.name)
        except OSError:
            continue
    return roles


def _panes() -> dict[str, tuple[str, str]] | None:
    """window name -> (foreground command, dead flag) for pane 1 across the
    plant's tmux sockets; None when no socket can be read."""
    found: dict[str, tuple[str, str]] = {}
    readable = False
    for socket in sorted(glob.glob(f"/tmp/tmux-{os.getuid()}/mishe-*")):
        try:
            out = _tmux(socket, "list-panes", "-a", "-F", "#{window_name}\t#{pane_index}\t#{pane_current_command}\t#{pane_dead}")
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode:
            continue
        readable = True
        for line in out.stdout.splitlines():
            window, index, command, dead = line.split("\t")
            if index == "1":
                found[window] = (command, dead)
    return found if readable else None


def check(home: Path, now: float | None = None) -> tuple[int, list[str]]:
    """0 healthy, 2 a standing problem, 3 undecidable. Returns (exit, lines)."""
    now = time.time() if now is None else now
    roles = claude_roles(home)
    if not roles:
        return 0, ["no claude-engine roles declared"]
    panes = _panes()
    if panes is None:
        return 3, ["cannot read any plant tmux socket"]
    problems, notes = [], []
    for role in roles:
        command, dead = panes.get(role, (None, None))
        if command is None:
            problems.append(f"{role}: no pane")
            continue
        if command != ENGINE or dead != "0":
            problems.append(f"{role}: pane runs {command!r} dead={dead} (expected live {ENGINE})")
            continue
        for suffix, limit in (("busy", STALE_BUSY), ("blocked", STALE_BLOCKED)):
            path = journal(home, role, suffix)
            if path.exists():
                age = now - path.stat().st_mtime
                if age > limit:
                    problems.append(f"{role}: {suffix} for {age / 60:.0f}min (limit {limit / 60:.0f}min)")
        if not journal(home, role, "alive").exists():
            problems.append(f"{role}: no SessionStart receipt")
        for suffix in ("failure", "hook-errors"):
            path = journal(home, role, suffix)
            if path.exists():
                problems.append(f"{role}: {suffix} recorded: {path.read_text().strip()[-160:]}")
        notes.append(f"{role}: live")
    return (2, problems) if problems else (0, notes)


def settings(home: Path, role: str, gate: Path) -> dict:
    cmd = lambda event: {"type": "command", "command": f"/usr/bin/python3 {gate} hook {event}", "timeout": 10}
    one = lambda event, matcher=None: [dict(({"matcher": matcher} if matcher else {}), hooks=[cmd(event)])]
    return {"hooks": {
        "SessionStart": one("SessionStart"), "SessionEnd": one("SessionEnd"),
        "UserPromptSubmit": one("UserPromptSubmit"), "Stop": one("Stop"),
        "StopFailure": one("StopFailure"),
        "Notification": one("PermissionPrompt", "permission_prompt"),
    }}


def install(home: Path, role: str, model: str, repo: Path) -> dict:
    home = home.resolve()
    gate_dir = home / "claude-gate"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gate = gate_dir / "claude_gate.py"
    shutil.copyfile(Path(__file__).resolve(), gate)
    gate.chmod(0o755)
    config = gate_dir / f"{role}.settings.json"
    config.write_text(json.dumps(settings(home, role, gate), indent=2) + "\n")
    probe_path = home / "checks" / "mind-ready" / role
    probe_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path.write_text(f"#!/bin/sh\nexec /usr/bin/python3 {gate} probe\n")
    probe_path.chmod(0o755)
    launcher = home / "minds" / role
    backup = home / "backups" / f"launcher-{role}-pre-claude"
    if launcher.exists() and f"# mishe-engine: {ENGINE}" not in launcher.read_text() and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(launcher, backup)
    launcher.write_text(
        f"#!/bin/sh\n# mishe-engine: {ENGINE}\n# model: {model}\n"
        f"export MISHE_GATE_HOME={home}\nexport MISHE_GATE_ROLE={role}\n"
        f"cd {repo}\n"
        f"exec claude --model {model} --permission-mode bypassPermissions "
        f"--setting-sources project,local --settings {config} "
        f"--append-system-prompt 'You are the {role} mind of the Mishe plant. Follow {repo}/AGENTS.md.'\n")
    launcher.chmod(0o755)
    return {"gate": str(gate), "settings": str(config), "probe": str(probe_path),
            "launcher": str(launcher), "revert": str(backup)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("hook"); p.add_argument("event")
    sub.add_parser("probe")
    p = sub.add_parser("check"); p.add_argument("--home", required=True); p.add_argument("--repo")
    p = sub.add_parser("install")
    p.add_argument("--home", required=True); p.add_argument("--role", required=True)
    p.add_argument("--model", required=True); p.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    if args.mode == "hook":
        if not (os.environ.get("MISHE_GATE_HOME") and os.environ.get("MISHE_GATE_ROLE")):
            print("claude_gate hook: MISHE_GATE_HOME and MISHE_GATE_ROLE are required", file=sys.stderr)
            return 1
        return hook(args.event, Path(os.environ.get("MISHE_GATE_HOME", "")), os.environ.get("MISHE_GATE_ROLE", ""))
    if args.mode == "probe":
        return probe()
    if args.mode == "check":
        code, lines = check(Path(args.home))
        print(("OK" if code == 0 else "FAIL" if code == 2 else "UNKNOWN") + ": " + "; ".join(lines))
        return code
    print(json.dumps(install(Path(args.home), args.role, args.model, Path(args.repo)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
