#!/usr/bin/env python3
"""Detectors for the network window and for the network mind's improvement.

  window    the outer tmux window "network" exists and its pane runs tools/network_view.py
  progress  the network mind's wall is fresh and network files were committed recently
  uplink    the WAN receive-drop rate (eth0 rx_dropped) from router readings stays under the bound

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import network_boundary as nb

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
    started = nb.process_start(pane_pid)
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


UPLINK_WINDOW_S = 2 * 3600     # the rate is judged over the last two hours of router readings
UPLINK_MIN_SPAN_S = 1.8 * 3600  # a RED rate needs the readings to actually cover that span
UPLINK_MAX_AGE_S = 900         # the newest router reading must be this fresh, else UNKNOWN
UPLINK_RED_PER_HOUR = 400      # rx_dropped per hour above this for the full span is RED


def uplink_state(rows, now):
    """Judge the WAN receive-drop rate from router readings (dicts with 'ts' and 'uplink').

    Returns (code, message): 0 GREEN, 2 RED, 3 UNKNOWN.  A dead or stale probe is UNKNOWN, never GREEN.
    A counter that went backwards (router reboot or driver reset) is UNKNOWN for this window."""
    fresh = []
    for row in rows:
        try:
            ts = float(row["ts"])
            up = row["uplink"]
            dropped = int(up["rx_dropped"])
            errors = int(up["rx_errors"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - ts <= UPLINK_WINDOW_S and ts <= now + START_SLACK_S:
            fresh.append((ts, dropped, errors))
    if not fresh:
        return 3, "network-care: no router uplink reading in the last two hours"
    fresh.sort()
    last_ts, last_drop, last_err = fresh[-1]
    if now - last_ts > UPLINK_MAX_AGE_S:
        return 3, f"network-care: newest router uplink reading is {(now - last_ts) / 60:.0f} min old"
    if last_err > 0:
        return 2, f"network-care: eth0 has {last_err} receive errors (a link fault, not drops)"
    if len(fresh) < 2:
        return 3, "network-care: only one router uplink reading; no rate yet"
    first_ts, first_drop, _ = fresh[0]
    span = last_ts - first_ts
    if last_drop < first_drop:
        return 3, "network-care: rx_dropped counter went backwards in this window (reset); no rate"
    if span <= 0:
        return 3, "network-care: router uplink readings do not span a time; no rate"
    per_hour = (last_drop - first_drop) * 3600.0 / span
    text = f"rx_dropped {per_hour:.0f}/h over {span / 3600:.1f} h"
    if per_hour > UPLINK_RED_PER_HOUR and span >= UPLINK_MIN_SPAN_S:
        return 2, f"network-care: {text}, above {UPLINK_RED_PER_HOUR}/h for the full span"
    return 0, f"network-care: uplink GREEN ({text})"


def uplink(home):
    path = home / "network" / "router.jsonl"
    rows = []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            tail = fh.read().decode("utf-8", "replace").splitlines()
        if size > 256 * 1024:
            tail = tail[1:]  # the first line may be cut mid-row
        for line in tail:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError as exc:
        print(f"network-care: router readings unreadable: {exc}", file=sys.stderr)
        return 3
    code, message = uplink_state(rows, time.time())
    print(message, file=sys.stderr if code else sys.stdout)
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("what", choices=("window", "progress", "uplink"))
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--session", default="plants")
    a = ap.parse_args()
    if a.what == "uplink":
        return uplink(a.home)
    return window(a.session, a.home, a.repo) if a.what == "window" else progress(a.home, a.repo)


if __name__ == "__main__":
    sys.exit(main())
