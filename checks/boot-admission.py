#!/usr/bin/env python3
"""Report boot admission using the current guard and producer evidence.

On 2026-11-20 the one-shot boot authorization expired. The revive service
later cleared the containment latch, and producers recovered separately. A failed
one-shot keeps admission RED after recovery with a next-boot decision. Current
latches and producer activity take precedence for immediate action.

The check writes only its own state file. See docs/recovery.md for authority
over the root-owned request and latch.

Exit status:
  0  guard ready, producers ran this boot and no failed admission observed
  2  containment still latched or admission failed with the guard ready
  3  evidence unavailable or admission still undecided
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

#: Allow boot to settle before reporting an otherwise unexplained latch.
LATCH_GRACE_SECONDS = 600
#: The root boot admission adapter uses the same freshness interval.
GUARD_MAX_AGE_SECONDS = 20

HUMAN_ACTION = (
    "HUMAN ROOT ACTION REQUIRED: from an SSH or human shell run "
    "`sudo python3 /usr/local/lib/mishe-containment/guard.py status` then "
    "`... rearm`, and start the enabled user services in stages. Mishe does "
    "NOT clear or renew /var/lib/mishe-containment/authorized-next-boot.json "
    "(docs/recovery.md)."
)
NEXT_BOOT_ACTION = "Human root decision pending for unattended boot (docs/recovery.md)."


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
    """Producers that are running now, or completed successfully this boot.

    Producers are oneshot timer units: between runs they are inactive, so a
    count of active units reads zero on a healthy plant.  A producer counts as
    admitted when it is activating/active, or when it has a successful exit
    this boot.  ExecMainExitTimestampMonotonic is measured from boot, so a
    non-zero value means it ran since this boot.  Failed runs do not count.
    """
    listing = ["systemctl", "--user", "list-units", PRODUCER_PATTERN,
               "--all", "--no-legend", "--no-pager", "--plain"]
    try:
        result = subprocess.run(listing, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Undecidable("cannot list producer units: %s" % exc)
    if result.returncode:
        raise Undecidable("listing producer units failed: %s"
                          % ((result.stderr or "").strip()[-300:]))
    names = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    if not names:
        return 0
    command = ["systemctl", "--user", "show", "--no-pager"] + names + [
        "-p", "Id", "-p", "ActiveState", "-p", "Result",
        "-p", "ExecMainExitTimestampMonotonic"]
    try:
        shown = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Undecidable("cannot read producer units: %s" % exc)
    if shown.returncode:
        raise Undecidable("reading producer units failed: %s"
                          % ((shown.stderr or "").strip()[-300:]))
    count = 0
    for block in shown.stdout.split("\n\n"):
        row = {}
        for line in block.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                row[key.strip()] = value.strip()
        if not row.get("Id"):
            continue
        running = row.get("ActiveState") in ("active", "activating", "reloading")
        ran_ok = (row.get("Result") == "success"
                  and row.get("ExecMainExitTimestampMonotonic", "0") not in ("", "0"))
        if running or ran_ok:
            count += 1
    return count


def assess(repo: pathlib.Path = REPO) -> dict:
    """Read the host and repository, return a verdict row.  Writes nothing."""
    boot = _boot_id()
    age = _boot_age()
    admit = _systemctl_show(ADMIT_UNIT, "LoadState", "ActiveState", "Result",
                            "ExecMainStatus", "ConditionResult")
    producers = _producer_count()
    status = _read_json(GUARD_RUN / "status.json")
    latches = _latches()

    load_state = admit.get("LoadState")
    admit_active = admit.get("ActiveState")
    if load_state != "loaded" or not admit_active:
        raise Undecidable("unit %s load state %s, active state %s"
                          % (ADMIT_UNIT, load_state or "missing", admit_active or "missing"))

    sampled_at = status.get("at")
    if type(sampled_at) not in (int, float):
        raise Undecidable("sentinel sample timestamp missing or malformed")
    now = time.time()
    if not now - GUARD_MAX_AGE_SECONDS <= sampled_at <= now:
        raise Undecidable("sentinel sample timestamp stale or in the future")
    sentinel_state = status.get("state")
    sentinel_mode = status.get("mode")
    if not all(isinstance(value, str) and value for value in (sentinel_state, sentinel_mode)):
        raise Undecidable("sentinel state or mode missing or malformed")
    admit_failed = admit_active == "failed"
    ready = sentinel_state == "READY" and sentinel_mode == "NORMAL" and not latches

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
        "human_action": None,
    }

    admission_failure = ""
    if admit_failed:
        admission_failure = (" %s still failed (result %s, status %s)."
                             % (ADMIT_UNIT, row["boot_admit_result"], row["boot_admit_status"]))
    if latches and (admit_failed or age >= LATCH_GRACE_SECONDS):
        row["state"] = "red"
        row["human_action"] = HUMAN_ACTION
        row["detail"] = (
            "containment latch %s still set after %.0fs uptime. Sentinel %s/%s.%s %s"
            % (",".join(latches), age, sentinel_state, sentinel_mode,
               admission_failure, HUMAN_ACTION))
        return row

    if ready and producers > 0:
        row["state"] = "red" if admit_failed else "green"
        row["detail"] = ("Sentinel READY/NORMAL with latch clear. "
                         "%d producer(s) running or completed this boot." % producers)
        if admit_failed:
            row["human_action"] = NEXT_BOOT_ACTION
            row["detail"] += " " + NEXT_BOOT_ACTION + admission_failure
        return row

    if ready and admit_failed:
        row["state"] = "red"
        row["detail"] = (
            "Sentinel READY/NORMAL with latch clear. No producers running or completed "
            "this boot. Inspect user producer timers and failed services.%s" % admission_failure)
        return row

    row["state"] = "unknown"
    row["detail"] = (
        "Boot admission undecided. Sentinel %s/%s, %d producer(s) running or completed, "
        "latches=%s, uptime %.0fs.%s" % (sentinel_state, sentinel_mode, producers,
                                        ",".join(latches) or "none", age, admission_failure))
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Report current boot admission and producer readiness")
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
