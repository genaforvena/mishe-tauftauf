#!/usr/bin/env python3
"""Plant the resident genome and witness channels on one Linux user account."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mishe_tauftauf import seed  # noqa: E402
from mishe_tauftauf.tmux import _tmux, owns_session  # noqa: E402


def unit_text(home: Path, session: str, slug: str, python: str) -> str:
    return (
        f"[Unit]\nDescription=Mishe {slug} resident channel\nAfter=default.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={ROOT}\n"
        f"Environment=PYTHONPATH={ROOT / 'src'}\n"
        f"Environment=PATH={home / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}\n"
        f"ExecStart={python} -m mishe_tauftauf --home {home} seed run --session {session} "
        f"--slug {slug} --interval 5 --self-pick-seconds 300 --clear-grace-seconds 30\n"
        "Restart=always\nRestartSec=15\n\n[Install]\nWantedBy=default.target\n"
    )


def plant(home: Path, session: str, engine_command: str, operator_window: str, persist: bool) -> None:
    home = home.resolve()
    if home.parent != ROOT:
        raise ValueError(f"site must be directly inside this repository: {ROOT}")
    if any(char.isspace() for char in str(home)):
        raise ValueError("site path with whitespace is not supported by generated systemd units")
    argv = shlex.split(engine_command)
    if not argv:
        raise ValueError("engine command is empty")
    if shutil.which(argv[0]) is None:
        raise ValueError(f"agent command unavailable: {argv[0]}")
    if operator_window in {"genome", "witness"}:
        raise ValueError("operator window needs its own name")
    for slug in ("genome", "witness"):
        seed.init(home, slug, engine_command)
    for slug in ("genome", "witness"):
        print(seed.start(home, session, slug, 5), flush=True)
    if not owns_session(home, session):
        raise RuntimeError(f"session {session} lost its site ownership marker")
    names = _tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines()
    if operator_window not in names:
        _tmux("new-window", "-d", "-t", session, "-n", operator_window, "-c", str(ROOT), "sh")
    if persist:
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        for slug in ("genome", "witness"):
            unit = home / f"mishe-seed-{slug}.service"
            unit.write_text(unit_text(home, session, slug, sys.executable), encoding="utf-8")
            linked = subprocess.run(["systemctl", "--user", "show", unit.name, "-p", "FragmentPath", "--value"],
                                    capture_output=True, text=True, check=True, env=env).stdout.strip()
            if linked and linked != str(unit):
                raise RuntimeError(f"service {unit.name} already belongs to {linked}")
            if not linked:
                subprocess.run(["systemctl", "--user", "link", str(unit)], check=True, env=env)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, env=env)
        for slug in ("genome", "witness"):
            unit = home / f"mishe-seed-{slug}.service"
            subprocess.run(["systemctl", "--user", "enable", "--now", unit.name], check=True, env=env)
    actual = set(_tmux("list-windows", "-t", session, "-F", "#{window_name}").stdout.decode().splitlines())
    required = {operator_window, "genome", "witness"}
    if not required <= actual:
        raise RuntimeError(f"missing windows: {sorted(required - actual)}")
    print(f"plant ready: session={session} windows={','.join(sorted(actual))} services={'enabled' if persist else 'skipped'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=ROOT / ".mishe-seed")
    parser.add_argument("--session", default="mishe-seed")
    parser.add_argument("--engine-command", default="codex")
    parser.add_argument("--operator-window", default="operator")
    parser.add_argument("--no-services", action="store_true", help="start panes without installing user services")
    args = parser.parse_args()
    plant(args.home, args.session, args.engine_command, args.operator_window, not args.no_services)


if __name__ == "__main__":
    main()
