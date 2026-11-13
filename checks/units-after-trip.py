#!/usr/bin/env python3
"""Detector and bounded action: enabled Mishe units left dead after a containment trip.

Observed 2026-11-13: the host guard stopped mishe-containment.service at 06:53:15Z and killed the sensor units
mishe-audio-listener, mishe-note3-sampler and mishe-unit-psi-sampler in the same second. Their Restart policy
then re-tried them, but the protected containment drop-in adds ConditionPathExists=!tripped to every mishe-*
unit, so each retry was skipped as an unmet condition and systemd did not try again. The units stayed dead for
3.7 h after the latch cleared and no signal said so (operator DM 300608).

For every enabled mishe-*.service with a Restart policy, while the latch is absent:
  * active, or start-limit-hit, or Restart=no: not dead (nothing to do);
  * DELIBERATE units (mishe-host-guard, stopped by design): never started by this check;
  * inactive, and the unit's last systemd journal event is an unmet-condition skip: dead after trip. Its
    time dead is measured from the first run that saw it dead (recorded in the site state file).
  * a dead unit that has been dead for at least --grace seconds is started once per dead episode with
    ``systemctl --user start`` when --act is given. The episode ends when the unit is seen active again.

While the latch is present, the units are held by design: GREEN, and every episode is cleared.

Only stdlib is used, so the check runs from committed HEAD bytes without importing the pinned release.

Exit status:
  0  no enabled mishe unit is dead after a trip (or the latch is present)
  2  at least one unit is dead after a trip (RED; the detail lists the names and minutes dead)
  3  undecidable: systemctl or journalctl could not be read (UNKNOWN)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

LATCH = Path("/var/lib/mishe-containment/tripped")
SKIP_MARK = "unmet condition check"
START_MARK = "Started "
DELIBERATE = frozenset({"mishe-host-guard.service"})
GRACE_SECONDS = 120
STATE_NAME = "units-after-trip.json"


def run_systemctl(args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def run_journal(args):
    return subprocess.run(["journalctl", "--user", *args], capture_output=True, text=True, timeout=30)


def enabled_units(run):
    """Enabled mishe-*.service unit names from the user unit-file listing."""
    res = run(["list-unit-files", "mishe-*.service", "--no-legend", "--no-pager"])
    if res.returncode != 0:
        raise RuntimeError(f"list-unit-files failed: {res.stderr.strip()}")
    names = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(".service") and parts[1] == "enabled":
            names.append(parts[0])
    return names


def unit_props(run, name):
    res = run(["show", name, "-p", "ActiveState,Result,Restart"])
    if res.returncode != 0:
        raise RuntimeError(f"show {name} failed: {res.stderr.strip()}")
    return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)


def last_event_is_skip(runj, name):
    """True when the unit's most recent start-or-skip journal event is an unmet-condition skip."""
    res = runj(["-u", name, "-n", "20", "-o", "cat", "--no-pager"])
    if res.returncode != 0:
        raise RuntimeError(f"journalctl {name} failed: {res.stderr.strip()}")
    for line in reversed(res.stdout.splitlines()):
        if SKIP_MARK in line:
            return True
        if START_MARK in line:
            return False
    return False


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    tmp.replace(path)


def evaluate(state: dict, *, now: float, latch_present: bool, run, runj, act: bool, grace: float):
    """Return (exit_code, lines, new_state). Pure except for the injected run/runj calls."""
    if latch_present:
        return 0, ["latch present: units are held by design; no episodes"], {}
    lines, dead_names, new_state, errors = [], [], {}, []
    for name in enabled_units(run):
        if name in DELIBERATE:
            continue
        # A failure on one unit must not lose the episodes already recorded for the others: a start that
        # happened is written to new_state (and so to disk) before the loop moves on.
        try:
            props = unit_props(run, name)
            if props.get("ActiveState") != "inactive" or props.get("Result") == "start-limit-hit" \
                    or props.get("Restart", "no") == "no":
                continue
            if not last_event_is_skip(runj, name):
                continue
            episode = state.get(name, {"dead_since": now, "started": False})
            age = now - float(episode["dead_since"])
            new_state[name] = episode
            dead_names.append(name)
            line = f"{name} dead {age / 60:.1f} min after trip"
            if act and age >= grace and not episode.get("started"):
                # Mark the episode started before the call: a failing or raising start is still one start.
                episode["started"] = True
                episode["started_at"] = now
                res = run(["start", name])
                line += f"; started once (exit {res.returncode})"
            lines.append(line)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
            if name in state and name not in new_state:
                new_state[name] = state[name]
    if errors:
        return 3, [f"cannot observe {len(errors)} unit(s): " + "; ".join(errors)] + lines, new_state
    if dead_names:
        head = f"dead after trip: {len(dead_names)}"
        return 2, [head] + lines, new_state
    return 0, ["dead after trip: 0"], {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--home", required=True, help="site home (state file lives here)")
    parser.add_argument("--act", action="store_true", help="start a unit dead past --grace, once per episode")
    parser.add_argument("--grace", type=float, default=GRACE_SECONDS)
    args = parser.parse_args(argv)
    state_path = Path(args.home) / STATE_NAME
    try:
        code, lines, new_state = evaluate(load_state(state_path), now=time.time(), latch_present=LATCH.exists(),
                                          run=run_systemctl, runj=run_journal, act=args.act, grace=args.grace)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"units-after-trip: cannot observe: {exc}")
        return 3
    # Saved even when some unit could not be read, so a start already made is never repeated.
    save_state(state_path, new_state)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
