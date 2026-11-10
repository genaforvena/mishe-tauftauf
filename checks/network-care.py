#!/usr/bin/env python3
"""Detectors for the network window and for the network mind's improvement.

  window    the outer tmux window "network" exists and its pane runs tools/network_view.py
  progress  the network mind's wall is fresh and network files were committed recently

Exit: 0 GREEN, 2 RED (the window is missing/wrong, or the mind has gone quiet), 3 UNKNOWN
(tmux or git cannot be read).  Nothing here holds a signal GREEN by hand.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

WALL_MAX_AGE_S = 6 * 3600
COMMIT_MAX_AGE_S = 48 * 3600
FILES = ("tools/network_view.py", "tools/network_boundary.py", "rust/mishe-network-probe", "checks/network-care.py")


RESPAWN = ("tmux respawn-pane -k -c {repo} -t {session}:network "
           "'env MISHE_TAUFTAUF_HOME=$MISHE_SEED_HOME python3 tools/network_view.py'")
WINDOW_FILES = ("tools/network_view.py", "tools/network_boundary.py")


def window_copy_stale(home, repo):
    """0 when the view's running copy started after the last commit touching its files, 2 when it is older
    or has not reported its code (a pre-fix copy never does), 3 when the history cannot be read."""
    try:
        state = json.loads((home / "network" / "window.json").read_text())
        started = float(state["started"])
    except (OSError, ValueError, KeyError, TypeError):
        print("network-care: the view has not reported its running copy (window.json missing)", file=sys.stderr)
        return 2
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", *WINDOW_FILES],
                           capture_output=True, text=True, timeout=30)
        stamp = r.stdout.strip()
        if r.returncode != 0 or not stamp.isdigit():
            raise ValueError(r.stderr)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"network-care: git history unreadable: {exc}", file=sys.stderr)
        return 3
    if started < int(stamp):
        print(f"network-care: the window's copy of the view is older than its last commit "
              f"({time.time() - started:.0f}s running, committed {(time.time() - int(stamp)) / 60:.0f} min ago)",
              file=sys.stderr)
        return 2
    return 0


def window(session, home, repo):
    try:
        r = subprocess.run(["tmux", "list-panes", "-t", f"{session}:network", "-F", "#{pane_dead} #{pane_start_command}"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"network-care: tmux unreadable: {exc}", file=sys.stderr)
        return 3
    if r.returncode != 0:
        if "no server" in r.stderr or "error connecting" in r.stderr:
            print("network-care: tmux server unreachable; cannot tell", file=sys.stderr)
            return 3
        print(f"network-care: no window {session}:network", file=sys.stderr)
        return 2
    if "network_view.py" not in r.stdout or r.stdout.startswith("1 "):
        print("network-care: window exists but is dead or not running network_view.py", file=sys.stderr)
        return 2
    stale = window_copy_stale(home, repo)
    if stale == 2:
        print("network-care: respawn it with: " + RESPAWN.format(session=session, repo=repo), file=sys.stderr)
        return 2
    if stale == 3:
        return 3
    print("network-care: window GREEN")
    return 0


def progress(home, repo):
    wall = home / "walls" / "network.md"
    try:
        age = time.time() - wall.stat().st_mtime
    except OSError:
        print("network-care: the network mind has no wall yet", file=sys.stderr)
        return 2
    if age > WALL_MAX_AGE_S:
        print(f"network-care: wall is {age / 3600:.1f}h old (bound {WALL_MAX_AGE_S // 3600}h)", file=sys.stderr)
        return 2
    try:
        r = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", *FILES],
                           capture_output=True, text=True, timeout=30)
        stamp = r.stdout.strip()
        if r.returncode != 0 or not stamp.isdigit():
            raise ValueError(r.stderr)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"network-care: git history unreadable: {exc}", file=sys.stderr)
        return 3
    since = time.time() - int(stamp)
    if since > COMMIT_MAX_AGE_S:
        print(f"network-care: no network improvement committed for {since / 3600:.0f}h", file=sys.stderr)
        return 2
    print(f"network-care: progress GREEN wall_age={age:.0f}s last_commit={since / 3600:.1f}h ago")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", choices=("window", "progress"))
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--session", default="plants")
    a = ap.parse_args()
    return window(a.session, a.home, a.repo) if a.what == "window" else progress(a.home, a.repo)


if __name__ == "__main__":
    sys.exit(main())
