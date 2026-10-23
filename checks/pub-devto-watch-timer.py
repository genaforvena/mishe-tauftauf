#!/usr/bin/env python3
"""Detector: the Pub DEV watch timer can be alive with nothing scheduled.

Measured on this plant, 2026-10-22 (wake 90679).

The five-minute read-only DEV watch is the only observation the Pub role has
of reader activity on the DEV account, and its timer is the only thing that
makes the scan happen. On 2026-10-22 the host rebooted at 11:44:36Z; the
timer was started at 11:45:22Z, 46 s later, and from then on it read:

    ActiveState=active
    SubState=elapsed
    NextElapseUSecRealtime=
    NextElapseUSecMonotonic=infinity

`active` with no elapse scheduled: the unit was alive and would never fire
again. The last scan was 11:26:11Z, so the observation aged past its 600 s
freshness limit and read UNKNOWN -- while the Pub top pane, which asked only
``systemctl --user is-active``, printed ``DEV WATCH TIMER: GREEN -- active,
five-minute read-only scans``.

**Why it had no elapse.** The unit declared ``OnBootSec=30`` and
``OnUnitActiveSec=5min``. The first is anchored to the boot, and the timer was
started after that boot-relative elapse had already passed; the second is
anchored to the target service's last activation, and the service had not run
since the boot. With both anchors past there is no future elapse to wait for.
A controlled A/B reproduced exactly that: the same unit shape, restarted once
its anchor had passed, reads ``elapsed`` with an infinite next elapse and
never fires; the same shape plus ``OnCalendar=*:0/5`` reads ``waiting`` with a
scheduled elapse under the same restart.

**The repair** is in the installed unit: a calendar anchor always has a future
elapse, whatever the boot timing or the target's history. That unit file is
site-local, so this check is the durable part. It reads

* the installed unit (``SITE/pub/systemd/<timer>``), for its anchors;
* the running timer state -- live from ``systemctl --user show``, or a
  captured state file (``--timer-state``) so a dead state stays evidence and
  the check stays deterministic off the plant;
* the published Pub dashboard frame (``SITE/dashboards/pub.json``), for the
  claim the pane makes about the watch.

and it refuses to call the watch healthy unless the unit declares an anchor
that always has a future elapse *and* the running timer will fire again.

**Two states that look the same, and are not.** While the watch service is
executing, the timer reads ``SubState=running`` with an empty ``NextElapse``:
systemd has not computed the next elapse yet. That is the healthy mid-scan
state, and reading it as "nothing scheduled" reports RED on a working timer
once per scan. The dead state is ``SubState=elapsed``, and the two are told
apart by ``SubState``, not by the emptiness of ``NextElapse``. For the same
reason the published frame is compared with the independently decided verdict
in both directions: a frame that says RED over a timer that will fire again
is a fault of its own kind, and a detector that cries wolf is not believed
when it is right.

Exit status:
  0  the unit declares a reboot-proof anchor and the running timer will fire
     again, and the published frame does not contradict that
  2  the unit can enter a state with no future elapse, or the running timer is
     in one, or the published frame contradicts the timer
  3  the question cannot be decided: no installed unit, and no captured or
     live timer state
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TIMER = "mishe-self-development-current-pub-devto-watch.timer"

# An anchor whose elapse is always in the future, whatever happened to the
# boot or to the target unit's last activation.  A calendar expression is
# recurring, so it always has a next occurrence.  ``OnActiveSec`` is *not*
# here: it is relative to the timer's own activation, so it elapses once and
# then the timer is done -- the same "alive with nothing scheduled" shape.
FUTURE_ANCHORS = ("OnCalendar",)
# Anchors that are relative to something that may already be in the past.
PAST_ANCHORS = ("OnBootSec", "OnUnitActiveSec", "OnUnitInactiveSec",
                "OnActiveSec")

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"


def parse_timer_unit(text: str) -> dict[str, str]:
    """Read the ``[Timer]`` section's directives out of a unit file."""
    directives: dict[str, str] = {}
    in_timer = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_timer = line == "[Timer]"
            continue
        if not in_timer or not line or line.startswith(("#", ";")):
            continue
        key, _, value = line.partition("=")
        directives[key.strip()] = value.strip()
    return directives


def anchors(directives: dict[str, str]) -> list[str]:
    """The schedule anchors the unit declares."""
    return [name for name in (*FUTURE_ANCHORS, *PAST_ANCHORS) if name in directives]


def has_future_anchor(directives: dict[str, str]) -> bool:
    """Whether the unit declares an anchor that always has a future elapse."""
    return any(name in directives for name in FUTURE_ANCHORS)


def parse_show(text: str) -> dict[str, str]:
    """Read the properties out of ``systemctl show`` output."""
    props: dict[str, str] = {}
    for raw in text.splitlines():
        key, _, value = raw.partition("=")
        if key:
            props[key.strip()] = value.strip()
    return props


def has_scheduled_elapse(props: dict[str, str]) -> bool:
    """Whether the timer has an elapse scheduled after now.

    ``NextElapseUSecMonotonic=0`` is systemd's "not applicable" marker for a
    timer whose next elapse is wall-clock based, so it is not an elapse.
    ``infinity`` means "never".
    """
    realtime = props.get("NextElapseUSecRealtime", "")
    if realtime and realtime != "infinity":
        return True
    monotonic = props.get("NextElapseUSecMonotonic", "")
    if not monotonic or monotonic in ("infinity", "0"):
        return False
    return True


def is_firing(props: dict[str, str]) -> bool:
    """Whether the timer is executing its service right now.

    While the service runs, systemd has not computed the next elapse yet, so
    ``NextElapse`` is empty.  That is the timer's normal mid-scan state, not
    the dead state: the timer fired, and a recurring anchor re-arms it when
    the service exits.  Reading an empty ``NextElapse`` here as "nothing
    scheduled" reports RED on a healthy timer once per scan.
    """
    return props.get("SubState") == "running"


def dashboard_claims_green(frame: str) -> bool:
    """Whether a dedicated dashboard line reports the watch as GREEN."""
    return re.search(r"^DEV WATCH TIMER: GREEN(?:\s|$)", frame, re.MULTILINE) is not None


def dashboard_claims_red(frame: str) -> bool:
    """Whether a dedicated dashboard line reports the watch as RED."""
    return re.search(r"^DEV WATCH TIMER: RED(?:\s|$)", frame, re.MULTILINE) is not None


def timer_verdict(props: dict[str, str]) -> tuple[str, str]:
    """Whether the running timer will fire again, and why.

    ``SubState`` is what distinguishes the dead state from the healthy one.
    ``elapsed`` is the dead state: the unit is active and has nothing left to
    wait for.  ``running`` means the timer is executing its service right now,
    so systemd has not computed the next elapse yet -- that is the healthy
    mid-scan state, and reading its empty ``NextElapse`` as "nothing
    scheduled" reports RED on a working timer once per scan.
    """
    if props.get("ActiveState") != "active":
        return RED, f"the timer is {props.get('ActiveState') or 'absent'}, not active"
    if props.get("SubState") == "elapsed":
        return RED, ("the timer is active and elapsed with no scheduled elapse; "
                     "it will not fire again")
    if is_firing(props):
        return GREEN, ("the timer is firing its service now, and the unit's "
                       "recurring anchor re-arms it when the service exits")
    if not has_scheduled_elapse(props):
        return RED, ("the timer is active with no scheduled elapse; it will not "
                     "fire again")
    return GREEN, "the timer is active with a scheduled elapse"


def verdict(directives: dict[str, str] | None,
            props: dict[str, str] | None,
            frame: str = "") -> tuple[str, str]:
    """The detector's decision, and the sentence that says why."""
    if directives is None:
        return UNKNOWN, "no installed timer unit: the schedule cannot be read"
    if not has_future_anchor(directives):
        return RED, ("the unit declares only anchors that can already be past ("
                     + ", ".join(anchors(directives) or ["none"])
                     + "); it can be active with nothing scheduled")
    if props is None:
        return UNKNOWN, "no timer state: the running timer could not be read"
    state, reason = timer_verdict(props)
    # The published frame is compared with the independently decided verdict,
    # in both directions.  A frame that says GREEN over a timer that cannot
    # fire is a fault of its own kind -- and so is a frame that says RED over
    # a timer that will fire again, which is how a detector that cries wolf
    # gets to look like a fault in the thing it watches.
    if frame and dashboard_claims_red(frame) and state == GREEN:
        return RED, ("the published dashboard reports the watch as RED while the "
                     "timer will fire again; the frame contradicts the timer")
    if frame and dashboard_claims_green(frame) and state == RED:
        return RED, ("the published dashboard reports the watch as GREEN while "
                     "the timer cannot fire")
    if state == RED:
        return RED, reason
    return GREEN, reason


def read_unit(home: Path) -> dict[str, str] | None:
    """The installed timer unit's directives, or None when it is not there."""
    path = home / "pub" / "systemd" / TIMER
    try:
        return parse_timer_unit(path.read_text())
    except OSError:
        return None


def live_timer_state() -> dict[str, str] | None:
    """The running timer's properties, or None when systemd cannot be asked."""
    try:
        show = subprocess.run(
            ["systemctl", "--user", "show", TIMER,
             "-p", "ActiveState", "-p", "SubState",
             "-p", "NextElapseUSecRealtime", "-p", "NextElapseUSecMonotonic"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if show.returncode != 0:
        return None
    return parse_show(show.stdout)


def read_frame(home: Path) -> str:
    """A fresh published Pub dashboard frame, or empty when absent/stale."""
    try:
        payload = json.loads((home / "dashboards" / "pub.json").read_text())
    except (OSError, ValueError):
        return ""
    frame = payload.get("frame")
    created = payload.get("created")
    if not isinstance(frame, str) or not isinstance(created, str):
        return ""
    try:
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if timestamp.tzinfo is None:
        return ""
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    return frame if 0 <= age <= 300 else ""


def default_home() -> Path | None:
    """The plant home, only when the plant explicitly provides it."""
    for name in ("MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default="",
                        help="the plant home (default: $MISHE_SEED_HOME)")
    parser.add_argument("--timer-state", default="",
                        help="a captured `systemctl --user show` output to read instead of asking systemd")
    args = parser.parse_args(argv)
    home = Path(args.home) if args.home else default_home()
    directives = read_unit(home) if home is not None else None
    if args.timer_state:
        try:
            props = parse_show(Path(args.timer_state).read_text())
        except OSError:
            props = None
    else:
        props = live_timer_state()
    frame = read_frame(home) if home is not None else ""

    state, reason = verdict(directives, props, frame)
    print(f"VERDICT: {state} — {reason}")
    if directives is not None:
        print(f"UNIT: {TIMER} anchors={','.join(anchors(directives)) or 'none'} "
              f"reboot_proof={has_future_anchor(directives)}")
    if props is not None:
        print(f"TIMER: ActiveState={props.get('ActiveState', '?')} "
              f"SubState={props.get('SubState', '?')} "
              f"next_realtime={props.get('NextElapseUSecRealtime', '') or 'none'} "
              f"next_monotonic={props.get('NextElapseUSecMonotonic', '') or 'none'}")
    if frame:
        print("DASHBOARD: " + ("claims the watch is GREEN" if dashboard_claims_green(frame)
                               else "does not claim the watch is GREEN"))
    return {GREEN: 0, RED: 2, UNKNOWN: 3}[state]


if __name__ == "__main__":
    sys.exit(main())
