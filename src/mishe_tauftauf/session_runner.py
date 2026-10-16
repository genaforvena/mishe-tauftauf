"""Readiness-aware adapter for the legacy scope-shaped session raise.

Keepers intentionally outlive their session: a tmux server may also host other
sessions. Never stop a keeper merely because this session disappeared. Serialize
raises and use a fresh service name so a stale keeper cannot prevent recovery.
This adapter accepts the pinned release's literal tmux argv, not shell prose.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path


def _stop_superseded_units(session: str, keep_unit: str) -> None:
    """Stop prior transient units for this session after a replacement is confirmed.

    The raise path intentionally never stops a keeper merely because its session
    disappeared (a server may host other sessions). But a superseded keeper whose
    replacement is confirmed running is a husk; stopping it prevents unbounded
    unit accumulation across raises.
    Matching is by exact generated unit identity: the complete session prefix plus
    exactly 32 lowercase hex characters and ``.service``. A broad prefix match would
    also stop keepers of longer session names sharing the prefix (session ``owned``
    would stop ``owned-session-other``'s keeper).
    """
    identity = re.compile(re.escape(session) + r"-session-[0-9a-f]{32}\.service")
    try:
        result = subprocess.run(
            ["systemctl", "--user", "list-units", "--all", "--no-legend", "--plain"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode:
        return
    stale = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0]
        if unit != keep_unit and identity.fullmatch(unit):
            stale.append(unit)
    for unit in stale:
        try:
            subprocess.run(["systemctl", "--user", "stop", unit],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass


def raise_session(session: str, window: str, workspace: str, lock_dir: Path,
                  timeout: float = 15.0, systemd_runner: str = "/usr/bin/systemd-run",
                  tmux_tmpdir: str | None = None) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", session):
        raise ValueError("invalid session identity")
    if not window or not Path(workspace).is_dir() or timeout <= 0:
        raise ValueError("invalid session window, workspace or timeout")
    lock_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    # An explicit per-site socket directory wins over the inherited one: a
    # supervisor launched without the unit's TMUX_TMPDIR must still raise on
    # its own site's socket, never the default one (duplicate-session fork).
    private_dir = tmux_tmpdir if tmux_tmpdir is not None else environment.get("TMUX_TMPDIR")
    if private_dir:
        environment.pop("TMUX", None)
        environment["TMUX_TMPDIR"] = private_dir
    def exists() -> bool:
        return subprocess.run(["tmux", "has-session", "-t", "=" + session],
                              capture_output=True, timeout=5, env=environment).returncode == 0
    with (lock_dir / (session + ".lock")).open("a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("UNKNOWN session startup lock deadline exceeded")
                time.sleep(.1)
        if exists():
            return
        unit = session + "-session-" + uuid.uuid4().hex + ".service"
        command = ["tmux", "new-session", "-d", "-s", session,
                   "-n", window, "-c", workspace, "sh"]
        if private_dir:
            # Transient services do not inherit their caller's environment.
            command = ["env", "-u", "TMUX", "TMUX_TMPDIR=" + private_dir, *command]
        leader = shlex.join(command) + " && exec sleep infinity"
        result = subprocess.run([systemd_runner, "--user", "--collect",
                                 "--unit", unit, "--description", "Mishe resident session " + session,
                                 "sh", "-c", leader], timeout=15)
        if result.returncode:
            raise RuntimeError("UNKNOWN session startup failed; systemd-run exit=" + str(result.returncode))
        while not exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("UNKNOWN session unavailable after startup deadline; inspect " + unit)
            time.sleep(.1)
        _stop_superseded_units(session, unit)
        print("session ready " + session + " keeper=" + unit, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", action="store_true")
    parser.add_argument("--scope", action="store_true")
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--unit", required=True)
    parser.add_argument("--description")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    # Literal boundary: only the installed legacy producer's argv is accepted.
    tmux = argparse.ArgumentParser(add_help=False)
    tmux.add_argument("program", choices=["tmux"])
    tmux.add_argument("action", choices=["new-session"])
    tmux.add_argument("-d", action="store_true", required=True)
    tmux.add_argument("-s", required=True)
    tmux.add_argument("-n", required=True)
    tmux.add_argument("-c", required=True)
    tmux.add_argument("shell", choices=["sh"])
    command = tmux.parse_args(args.command)
    locks = Path(os.environ.get("MISHE_SESSION_LOCK_DIR", os.environ.get("XDG_RUNTIME_DIR", "/tmp"))) / "mishe-session-raises"
    try:
        raise_session(command.s, command.n, command.c, locks)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("session runner: " + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
