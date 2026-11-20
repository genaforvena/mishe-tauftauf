#!/usr/bin/env python3
"""Detector: after a boot, is Mishe admitted to run, or stuck needing a human?

The gap this closes.  On 2026-11-20 the host rebooted into a new kernel at
14:39Z (the previous boot had ended at 13:08Z).  The one-shot
``plant-mishe-boot-admit.service`` failed with "authorization expired": the
root-owned request ``/var/lib/mishe-containment/authorized-next-boot.json``
carried a deadline the roughly 91-minute outage passed.  By design ("no
incident autorecovery") no producer started.  ``docs/recovery.md`` is explicit
that Mishe does not clear or renew that request: a human decides, and the unit
stays failed until then.

Nothing watched for that state.  ``signal-coverage`` went RED only because
about sixty producers were stale, roughly 17 minutes after boot, and the
waking mind had to reconstruct the cause from reboot history, unit state and
the guard's status.  From a liveness view an expired authorization, an
unauthorized boot and a skipped admission all look the same: nothing running.
This detector names the state and the exact human action instead of leaving it
as a pile of unrelated stale-signal causes.

The detector answers, read-only:

  1. Did this boot's admission succeed, fail, or get skipped?
  2. Is the containment latch still set, and are producers still absent?
  3. If admission is stuck, what must a human do, and what must Mishe not do?

It writes only its own state file; it starts no unit and clears no latch.
An unreadable host is UNKNOWN (exit 3), never a pass and never a fault.

Exit status:
  0  Mishe is admitted and producers are running
  2  admission is stuck and the plant is down: a human root decision is owed
  3  the evidence needed to decide cannot be read
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import time

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Root-owned guard state and run directories (never written by this check).
GUARD_STATE = pathlib.Path("/var/lib/mishe-containment")
GUARD_RUN = pathlib.Path("/run/mishe-containment")
BOOT_ID = pathlib.Path("/proc/sys/kernel/random/boot_id")
PROC_STAT = pathlib.Path("/proc/stat")

ADMIT_UNIT = "plant-mishe-boot-admit.service"
PRODUCER_PATTERN = "mishe-producer-*.service"

#: A containment trip auto-rearms after a 120 s healthy interval.  A latch that
#: still holds after this grace is not a transient trip; it is a stuck plant.
LATCH_GRACE_SECONDS = 600

HUMAN_ACTION = (
    "HUMAN ROOT ACTION REQUIRED: from an SSH or human shell run "
    "`sudo python3 /usr/local/lib/mishe-containment/guard.py status` then "
    "`... rearm`, and start the enabled user services in stages. Mishe does "
    "NOT clear or renew /var/lib/mishe-containment/authorized-next-boot.json "
    "(docs/recovery.md)."
)


class Undecidable(Exception):
    """The question cannot be decided from here; the answer is UNKNOWN."""


def _read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Undecidable("cannot read %s: %s" % (path, exc))


def _read_json(path: pathlib.Path) -> dict:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Undecidable("cannot read %s: %s" % (path, exc))
    if not isinstance(row, dict):
        raise Undecidable("%s is not a JSON object" % path)
    return row


def _boot_id() -> str:
    return _read_text(BOOT_ID).strip()


def _boot_age() -> float:
    """Seconds since boot, from btime in /proc/stat (no external command)."""
    for line in _read_text(PROC_STAT).splitlines():
        if line.startswith("btime "):
            try:
                return max(0.0, time.time() - float(line.split()[1]))
            except (IndexError, ValueError):
                break
    raise Undecidable("cannot read boot time from /proc/stat")


def _latches() -> list:
    return [name for name in ("tripped", "emergency") if (GUARD_STATE / name).exists()]


def _systemctl_show(unit: str, *props: str) -> dict:
    command = ["systemctl", "show", unit]
    for prop in props:
        command += ["-p", prop]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Undecidable("cannot read unit %s: %s" % (unit, exc))
    if result.returncode:
        raise Undecidable("systemctl show %s failed: %s"
                          % (unit, (result.stderr or "").strip()[-300:]))
    row = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            row[key.strip()] = value.strip()
    return row


def _producer_count() -> int:
    """Number of currently active mishe producer services."""
    command = ["systemctl", "--user", "list-units", PRODUCER_PATTERN,
               "--state=active", "--no-legend", "--no-pager"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Undecidable("cannot list producer units: %s" % exc)
    if result.returncode:
        raise Undecidable("listing producer units failed: %s"
                          % ((result.stderr or "").strip()[-300:]))
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def assess(repo: pathlib.Path = REPO) -> dict:
    """Read the host and repository, return a verdict row.  Writes nothing."""
    boot = _boot_id()
    age = _boot_age()
    latches = _latches()
    status = _read_json(GUARD_RUN / "status.json")
    admit = _systemctl_show(ADMIT_UNIT, "LoadState", "ActiveState", "Result",
                            "ExecMainStatus", "ConditionResult")
    producers = _producer_count()

    if admit.get("LoadState", "") == "not-found":
        raise Undecidable("unit %s is not installed" % ADMIT_UNIT)

    sentinel_state = status.get("state")
    sentinel_mode = status.get("mode")
    admit_active = admit.get("ActiveState", "")
    admit_failed = admit_active == "failed"

    row = {
        "boot_id": boot,
        "boot_age_seconds": round(age, 1),
        "boot_admit_active": admit_active,
        "boot_admit_result": admit.get("Result", ""),
        "boot_admit_status": admit.get("ExecMainStatus", ""),
        "boot_admit_condition": admit.get("ConditionResult", ""),
        "latches_present": sorted(latches),
        "sentinel_state": sentinel_state,
        "sentinel_mode": sentinel_mode,
        "producers_active": producers,
        "latch_grace_seconds": LATCH_GRACE_SECONDS,
        "human_action": HUMAN_ACTION,
    }

    if producers > 0 and sentinel_state == "READY" and sentinel_mode == "NORMAL":
        row["state"] = "green"
        row["detail"] = ("Mishe admitted this boot: sentinel %s/%s, %d producer(s) active"
                         % (sentinel_state, sentinel_mode, producers))
        if admit_failed:
            row["detail"] += ("; note: %s still failed (result %s, status %s)"
                              % (ADMIT_UNIT, row["boot_admit_result"], row["boot_admit_status"]))
        return row

    if admit_failed:
        row["state"] = "red"
        row["detail"] = (
            "%s failed (result %s, status %s) after %.0fs uptime; no producers "
            "running; boot admission was rejected and needs a human decision. %s"
            % (ADMIT_UNIT, row["boot_admit_result"], row["boot_admit_status"],
               age, HUMAN_ACTION))
        return row

    if latches and age >= LATCH_GRACE_SECONDS:
        row["state"] = "red"
        row["detail"] = (
            "containment latch %s still set after %.0fs uptime (grace %ss); sentinel "
            "%s/%s, %d producer(s) running; the plant was not admitted after boot. %s"
            % (",".join(latches), age, LATCH_GRACE_SECONDS, sentinel_state,
               sentinel_mode, producers, HUMAN_ACTION))
        return row

    row["state"] = "unknown"
    row["detail"] = (
        "plant is not up (sentinel %s/%s, %d producer(s)) but admission is not "
        "yet decisively stuck (admit=%s, latches=%s, uptime %.0fs); recheck before "
        "acting" % (sentinel_state, sentinel_mode, producers, admit_active,
                    ",".join(latches) or "none", age))
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Report whether Mishe was admitted after this boot or needs a human")
    parser.add_argument("--repo", type=pathlib.Path, default=REPO,
                        help="reviewed repository holding the check")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    parser.add_argument("--state", type=pathlib.Path, default=None,
                        help="write the verdict to this JSON file")
    args = parser.parse_args(argv)
    try:
        row = assess(args.repo)
    except Undecidable as exc:
        unknown = {"state": "UNKNOWN", "reason": str(exc)}
        if args.json:
            print(json.dumps(unknown, indent=1))
        else:
            print("UNKNOWN — " + str(exc))
        if args.state:
            args.state.parent.mkdir(parents=True, exist_ok=True)
            args.state.write_text(json.dumps(unknown, indent=1), encoding="utf-8")
        return 3
    if args.json:
        print(json.dumps(row, indent=1, sort_keys=True))
    else:
        print("%s: %s" % (row["state"].upper(), row["detail"]))
    if args.state:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(row, indent=1, sort_keys=True), encoding="utf-8")
    if row["state"] == "red":
        return 2
    if row["state"] == "unknown":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
