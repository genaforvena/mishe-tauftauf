"""Persistent operator shell and request dashboard in the owned tmux session."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from pathlib import Path

from .tmux import _python_command, _tmux, owns_session


def ensure(home: Path, session: str, interval: float = 5) -> str:
    home = home.resolve()
    if not owns_session(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    permit = bin_dir / "permit"
    if not permit.exists():
        permit.write_text("#!/bin/sh\nexec \"$(dirname \"$0\")/mishe-tauftauf\" --home " +
                          shlex.quote(str(home)) + " access \"$@\"\n", encoding="utf-8")
        permit.chmod(0o755)
    shell = bin_dir / "permissions-shell"
    if not shell.exists():
        shell.write_text("#!/bin/sh\nexport PATH=" + shlex.quote(str(bin_dir)) + ":\"$PATH\"\n"
                         "printf '%s\\n' 'Operator decisions: permit list | permit grant ID | permit revoke ID'\n"
                         "exec bash --noprofile --norc -i\n", encoding="utf-8")
        shell.chmod(0o755)
    top = home / "top-pains" / "permissions"
    if not top.exists():
        top.parent.mkdir(parents=True, exist_ok=True)
        top.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                       " -m mishe_tauftauf.seed_culture_views --home " + shlex.quote(str(home)) +
                       " --view permissions\n", encoding="utf-8")
        top.chmod(0o755)
    names = _tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines()
    created = "permissions" not in names
    if created:
        _tmux("new-window", "-d", "-t", session, "-n", "permissions", "-c", str(home.parent), "sh")
    target = f"{session}:permissions"
    panes = _tmux("list-panes", "-t", target, "-F", "#{pane_index}").stdout.decode().splitlines()
    if "1" not in panes:
        _tmux("split-window", "-v", "-t", target, "-c", str(home.parent), "sh")
        created = True
    _tmux("set-option", "-p", "-t", f"{target}.0", "remain-on-exit", "on")
    _tmux("set-option", "-p", "-t", f"{target}.1", "remain-on-exit", "on")
    _tmux("select-layout", "-t", target, "even-vertical")
    top_dead = _tmux("display-message", "-p", "-t", f"{target}.0", "#{pane_dead}").stdout.decode().strip() == "1"
    bottom_dead = _tmux("display-message", "-p", "-t", f"{target}.1", "#{pane_dead}").stdout.decode().strip() == "1"
    if created or top_dead:
        package_root = str(Path(__file__).resolve().parents[1])
        python_path = os.pathsep.join(part for part in (package_root, os.environ.get("PYTHONPATH", "")) if part)
        _tmux("respawn-pane", "-k", "-t", f"{target}.0", "env", f"MISHE_SEED_SESSION={session}",
              f"PYTHONPATH={python_path}",
              *_python_command("--home", str(home), "pain", "watch", "permissions", "--interval", str(interval)))
    if created or bottom_dead:
        _tmux("respawn-pane", "-k", "-t", f"{target}.1", str(shell))
    return f"permissions panel ready in {session}"


def run(home: Path, session: str, interval: float = 5) -> None:
    if interval <= 0:
        raise ValueError("interval must be positive")
    while True:
        print(ensure(home, session, interval), flush=True)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    if args.follow:
        run(args.home, args.session, args.interval)
    else:
        print(ensure(args.home, args.session, args.interval))


if __name__ == "__main__":
    main()
