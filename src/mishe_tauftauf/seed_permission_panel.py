"""Persistent operator shell and request dashboard in the owned tmux session."""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from pathlib import Path

from .runtime_source import python_search_path
from .tmux import _python_command, _tmux, owns_session


def write_launchers(home: Path) -> bool:
    """Upgrade only our generated launcher, preserving operator customizations.

    Returns True when a launcher was written or upgraded. The generated selector
    is already correct on every later call, so a steady resident loop must not
    rewrite it — the rewrite bumped its mtime on every interval — nor report a
    change it did not make.
    """
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    permit = bin_dir / "permit"
    if not permit.exists():
        permit.write_text("#!/bin/sh\nexec \"$(dirname \"$0\")/mishe-tauftauf\" --home " +
                          shlex.quote(str(home)) + " access \"$@\"\n", encoding="utf-8")
        permit.chmod(0o755)
        changed = True
    shell = bin_dir / "permissions-shell"
    legacy = ("#!/bin/sh\nexport PATH=" + shlex.quote(str(bin_dir)) + ":\"$PATH\"\n"
              "printf '%s\\n' 'Operator decisions: permit list | permit grant ID | permit revoke ID'\n"
              "exec bash --noprofile --norc -i\n")
    generated = ("#!/bin/sh\n# mishe-generated permissions selector\nexport PATH=" + shlex.quote(str(bin_dir)) + ":\"$PATH\"\n"
                 "permit menu\n"
                 "printf '%s\\n' 'Permissions: permit (selector) | permit revoke (selector) | permit list'\n"
                 "exec bash --noprofile --norc -i\n")
    current = shell.read_text(encoding="utf-8") if shell.exists() else None
    if current is None or (current in {legacy, generated} and current != generated):
        shell.write_text(generated, encoding="utf-8")
        shell.chmod(0o755)
        changed = True
    return changed


def ensure(home: Path, session: str, interval: float = 5) -> str:
    """Establish the panel, returning a readiness line only when it changed.

    The panel is idempotent, so a steady resident loop calling this every
    ``interval`` seconds must not report a constant line: an unchanged panel
    returns an empty string, and only a created window, a respawned pane or a
    created or upgraded launcher is a reportable transition.
    """
    home = home.resolve()
    if not owns_session(home, session):
        raise ValueError(f"session {session} is not owned by {home}")
    acted = write_launchers(home)
    shell = home / "bin" / "permissions-shell"
    top = home / "top-pains" / "permissions"
    if not top.exists():
        top.parent.mkdir(parents=True, exist_ok=True)
        top.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                       " -m mishe_tauftauf.seed_culture_views --home " + shlex.quote(str(home)) +
                       " --view permissions\n", encoding="utf-8")
        top.chmod(0o755)
        acted = True
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
    if created or top_dead or bottom_dead:
        # A pane respawned without an explicit PYTHONPATH inherits the tmux
        # server's environment, which keeps whatever release the server was
        # started with: the selector imports that stale code for as long as the
        # pane lives, and only the plant's idle refresh can correct it later.
        # Pass this process's own import root, the same one the mind panes get.
        package_root = str(Path(__file__).resolve().parents[1])
        python_path = python_search_path(package_root, os.environ.get("PYTHONPATH", ""))
    if created or top_dead:
        _tmux("respawn-pane", "-k", "-t", f"{target}.0", "env", f"MISHE_SEED_SESSION={session}",
              f"PYTHONPATH={python_path}",
              *_python_command("--home", str(home), "pain", "watch", "permissions", "--interval", str(interval)))
    if created or bottom_dead:
        _tmux("respawn-pane", "-k", "-t", f"{target}.1", "env", f"PYTHONPATH={python_path}", str(shell))
    if acted or created or top_dead or bottom_dead:
        return f"permissions panel ready in {session}"
    return ""


def run(home: Path, session: str, interval: float = 5) -> None:
    """Keep the panel established; report only the transitions ``ensure`` names."""
    if interval <= 0:
        raise ValueError("interval must be positive")
    while True:
        status = ensure(home, session, interval)
        if status:
            print(status, flush=True)
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
        status = ensure(args.home, args.session, args.interval)
        if status:
            print(status)


if __name__ == "__main__":
    main()
