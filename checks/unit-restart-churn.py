#!/usr/bin/env python3
"""Detector: a user unit restarting in a loop before systemd's start limit trips.

Observed 2026-11-24: light-cycle-monitor.service has Restart=always with a 5 s restart delay and no OnFailure
route for a kill. A SIGKILL restarts it in 5 s without the unit ever entering "failed", so OnFailure= never fires;
it only fires once StartLimitBurst (5 starts in 5 min) is exhausted. A crash loop therefore stays invisible to the
signal layer until it trips. This check reads NRestarts and watches its growth over a sliding window.

For each --unit (default light-cycle-monitor.service):
  * the NRestarts count and ActiveState are sampled and appended to the site state file;
  * churn = NRestarts now minus the oldest sample inside the window (--window seconds, default 900);
  * churn >= --threshold (default 3) is RED (exit 2), naming the unit, its churn and its ActiveState;
  * a counter that goes down (the unit was stopped and started by hand) restarts the baseline, not a RED;
  * the first sample of a unit is a baseline: GREEN with "baseline recorded".

Only stdlib is used. Exit status:
  0  no unit is churning (or baseline recorded)
  2  at least one unit restarted at least --threshold times inside the window (RED)
  3  undecidable: systemctl could not be read or the state file could not be saved (UNKNOWN)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_UNITS = ("light-cycle-monitor.service",)
WINDOW_SECONDS = 900
THRESHOLD = 3
STATE_NAME = "unit-restart-churn.json"


def run_systemctl(args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def unit_counts(run, name):
    res = run(["show", name, "-p", "ActiveState,NRestarts"])
    if res.returncode != 0:
        raise RuntimeError(f"show {name} failed: {res.stderr.strip()}")
    props = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
    if "NRestarts" not in props or "ActiveState" not in props:
        raise RuntimeError(f"show {name} returned no NRestarts/ActiveState")
    return int(props["NRestarts"]), props["ActiveState"]


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    tmp.replace(path)


def evaluate(state, units, now, run, window, threshold):
    """Return (exit code, report lines, new state). Pure apart from the injected run()."""
    lines, churning, errors = [], [], []
    new_state = {}
    for name in units:
        try:
            count, active = unit_counts(run, name)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
            if name in state:
                new_state[name] = state[name]
            continue
        # A counter that went down means the unit was reset by hand: every older sample then has a higher count
        # and is dropped, so the window restarts from this sample as the baseline.
        samples = [s for s in state.get(name, []) if now - s[0] <= window and s[1] <= count]
        samples.append([now, count])
        new_state[name] = samples[-200:]
        if len(samples) == 1:
            lines.append(f"{name}: baseline recorded (NRestarts={count}, {active})")
            continue
        churn = count - samples[0][1]
        span_min = (now - samples[0][0]) / 60
        line = f"{name}: {churn} restart(s) in {span_min:.1f} min (NRestarts={count}, {active})"
        lines.append(line)
        if churn >= threshold:
            churning.append(line)
    if errors:
        return 3, [f"cannot observe {len(errors)} unit(s): " + "; ".join(errors)] + lines, new_state
    if churning:
        return 2, [f"restart loop: {len(churning)} unit(s) over {threshold} restarts in {window // 60} min"] + lines, \
            new_state
    return 0, [f"restart churn: 0 unit(s) over {threshold} in {window // 60} min"] + lines, new_state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--home", required=True, help="site home (state file lives here)")
    parser.add_argument("--unit", action="append", help="user unit to watch (repeatable)")
    parser.add_argument("--window", type=float, default=WINDOW_SECONDS)
    parser.add_argument("--threshold", type=int, default=THRESHOLD)
    args = parser.parse_args(argv)
    units = args.unit or list(DEFAULT_UNITS)
    state_path = Path(args.home) / STATE_NAME
    code, lines, new_state = evaluate(load_state(state_path), units, time.time(), run_systemctl,
                                      args.window, args.threshold)
    print("\n".join(lines))
    try:
        save_state(state_path, new_state)
    except OSError as exc:
        print(f"unit-restart-churn: cannot save state: {exc}")
        return 3
    return code


if __name__ == "__main__":
    sys.exit(main())
