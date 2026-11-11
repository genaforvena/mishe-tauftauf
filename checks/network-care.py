#!/usr/bin/env python3
"""Detectors for the network window and for the network mind's improvement.

  window    the outer tmux window "network" exists and its pane runs tools/network_view.py
  progress  the network mind's wall is fresh and network files were committed recently

Exit: 0 GREEN, 2 RED (the window is missing/wrong, or the mind has gone quiet), 3 UNKNOWN
(tmux or git cannot be read).  Nothing here holds a signal GREEN by hand.
"""
import argparse
import hashlib
import json
import os
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
    """0 when the view's running copy is the code on disk now, 2 when it differs (a copy from before the
    last change, which the view itself should have reloaded) or it never reported its copy, 3 when the
    files cannot be read.  Compared by content, so a commit of unchanged code is not a false RED."""
    try:
        state = json.loads((home / "network" / "window.json").read_text())
        running = str(state["fingerprint"])
    except (OSError, ValueError, KeyError, TypeError):
        print("network-care: the view has not reported its running copy (window.json missing or pre-fingerprint)",
              file=sys.stderr)
        return 2
    try:
        disk = hashlib.sha256()
        for rel in WINDOW_FILES:
            disk.update((repo / rel).read_bytes())
    except OSError as exc:
        print(f"network-care: view files unreadable: {exc}", file=sys.stderr)
        return 3
    if running != disk.hexdigest():
        print("network-care: the window runs a copy of the view that differs from the files on disk "
              f"(running since {time.strftime('%H:%M:%SZ', time.gmtime(float(state.get('started', 0))))})",
              file=sys.stderr)
        return 2
    return 0


START_SLACK_S = 5          # clock-tick rounding between process start and the view's own timestamp
MAX_START_LAG_S = 600      # the view stamps "started" during its own startup, well within this


def process_argv(pid):
    """The argument vector of the live process with this pid, as a list (None when unreadable)."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def is_view_argv(argv):
    """True only when the process is an interpreter running tools/network_view.py as its script: the
    first argument that is not an interpreter option must be the script itself, not -c/-m code, and a
    script merely mentioning network_view.py later in argv does not count."""
    if not argv or not Path(argv[0]).name.startswith("python"):
        return False
    for arg in argv[1:]:
        if arg in ("-c", "-m"):
            return False
        if arg.startswith("-"):
            continue
        return Path(arg).name == "network_view.py"
    return False


def process_start(pid):
    """Epoch seconds at which the live process with this pid started (None when unreadable).  Used to
    tell the recorded view from a later process that reused its pid."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat.rsplit(")", 1)[1].split()
        ticks = int(fields[19])  # field 22 (starttime), counted after the parenthesised command name
        btime = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                     if line.startswith("btime "))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def window_process(home, pane_pid, pane_cmd):
    """0 when the process running in the pane right now is the recorded view, 2 when it is not (a pane
    that was respawned, a process other than the view, a window state that names another pid, or a
    pid that was reused after the view started), 3 when the process cannot be read.  pane_start_command
    alone is not enough: it names what the pane was started with, not what runs in it now."""
    if not pane_pid.isdigit():
        print("network-care: tmux did not report the pane's process; cannot tell", file=sys.stderr)
        return 3
    argv = process_argv(pane_pid)
    if argv is None:
        print(f"network-care: pane process {pane_pid} is not readable; cannot tell", file=sys.stderr)
        return 3
    if not is_view_argv(argv):
        print(f"network-care: pane process {pane_pid} runs {' '.join(argv)!r}, not the network view", file=sys.stderr)
        return 2
    try:
        state = json.loads((home / "network" / "window.json").read_text())
        recorded = str(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0  # no state yet: window_copy_stale reports that case
    if recorded != pane_pid:
        print(f"network-care: the pane runs pid {pane_pid} but the view recorded pid {recorded} "
              "(its fingerprint describes another process)", file=sys.stderr)
        return 2
    started = process_start(pane_pid)
    try:
        recorded_start = float(state["started"])
    except (KeyError, ValueError, TypeError):
        print("network-care: the view recorded no start time; cannot tell whether its pid was reused", file=sys.stderr)
        return 3
    if started is None:
        print(f"network-care: pane process {pane_pid} start time is not readable; cannot tell", file=sys.stderr)
        return 3
    lag = recorded_start - started
    if not (-START_SLACK_S <= lag <= MAX_START_LAG_S):
        print(f"network-care: pid {pane_pid} started {lag:+.0f}s from the view's recorded start; "
              "the pid was reused or the state is stale", file=sys.stderr)
        return 2
    return 0


def window(session, home, repo):
    try:
        r = subprocess.run(["tmux", "list-panes", "-t", f"{session}:network", "-F",
                            "#{pane_dead} #{pane_pid} #{pane_start_command}"],
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
    fields = r.stdout.split(maxsplit=2)
    if len(fields) < 3 or "network_view.py" not in fields[2] or fields[0] != "0":
        print("network-care: window exists but is dead or not running network_view.py", file=sys.stderr)
        return 2
    identity = window_process(home, fields[1], fields[2])
    if identity != 0:
        return identity
    stale = window_copy_stale(home, repo)
    if stale == 2:
        print("network-care: respawn it with: " + RESPAWN.format(session=session, repo=repo.resolve()), file=sys.stderr)
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
