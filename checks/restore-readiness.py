#!/usr/bin/env python3
"""Detector: is the host's containment latch actually clearable, and why not?

The gap this closes.  Restoring the plant's containment latch is a staged
operation whose one hard gate is the guard's own 120-second continuous-healthy
interval (``STABLE_SECONDS`` in the guard).  That gate is computed from
``clear_to_rearm(row)``, which requires ``io_pressure < 10`` and
``memory_pressure < 1``.  The guard's *warning* threshold is five times
higher: ``pressure_warning`` fires at ``io_pressure >= 25``.

  => a host with 10 <= io_pressure < 25 fails the restore gate while the
     operator sees ``pressure_warning: false`` and no pressure receipt,
     because receipts are only written when the warning *changes*.

So a restore can be refused with "continuous healthy stability interval
incomplete" for a reason that nothing on the plant reports.  On 2026-10-30 the
plant sat latched for hours while every visible signal said healthy: the
guard's own ``status`` reported ``clear_since`` as a large number, which read
as "the interval has held for hours", and the dashboard said nothing was
wrong.  The interval had in fact been reset repeatedly, and the restore's own
gate was never satisfied.

This detector answers, read-only, the question an operator actually has
before asking for a restore:

  1. Is a latch present, and is it the one the evidence names?
  2. Is the sentinel fresh, and does the host have headroom?
  3. How long has the continuous-healthy interval actually been held, measured
     the way the guard measures it -- ``now_monotonic - clear_since``, not the
     raw stamp, which is a monotonic timestamp and not elapsed seconds?
  4. If the interval is not long enough, which field is holding it back?

It also reports whether the plant's *own* restore path could run at all.  A
latch that is ready to clear is useless if the unit meant to clear it is gated
on the latch not existing.  See the second gap below.

The second gap.  systemd applies drop-in files in lexicographic order by
*file name*, regardless of which directory they are in, and a file further
down the prefix hierarchy wins only when the names are equal.  The plant's
catch-all drop-in is ``mishe-.service.d/zzzzzz-host-containment.conf``; it
sets ``Slice=mishe-plants.slice`` and
``ConditionPathExists=!/var/lib/mishe-containment/tripped``.  A unit with its
own drop-in named ``override.conf`` is overridden by the catch-all, because
``override.conf`` sorts before ``zzzzzz-host-containment.conf``.  The restore
unit is named exactly that way, and it is the one protected unit whose own
drop-in directory does not contain its own copy of the catch-all.  The
consequence is that the only unit whose job is to clear the latch is gated on
the latch not existing, so it can never run.

Nothing reported either of these.  This detector does.

The detector reads the host and the repository.  It writes nothing except its
own state file, it starts no unit, and it clears no latch.  An unreadable
host is UNKNOWN (exit 3), never a pass and never a fault.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Where the guard keeps its latches and its run directory.
GUARD_STATE = pathlib.Path("/var/lib/mishe-containment")
GUARD_RUN = pathlib.Path("/run/mishe-containment")
GUARD_CONFIG = pathlib.Path("/etc/mishe-containment.json")
GUARD_SOURCE = pathlib.Path("/usr/local/lib/mishe-containment/guard.py")

#: The guard's constants, read from the installed bytes when they can be read.
STABLE_SECONDS = 120
GATE_IO_PRESSURE = 10
GATE_MEMORY_PRESSURE = 1
WARNING_IO_PRESSURE = 25
WARNING_MEMORY_PRESSURE = 10

#: The plant's catch-all drop-in, and the name of the file that carries it.
CATCH_ALL_DIR = "mishe-.service.d"
CATCH_ALL_FILE = "zzzzzz-host-containment.conf"

#: The restore unit the plant registers as protected.
RESTORE_UNIT = "mishe-restore-2147b.service"

#: Drop-in names that sort before the catch-all and therefore lose to it.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@-]+\.conf$")


class Undecidable(Exception):
    """The question cannot be decided from here; the answer is UNKNOWN."""


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Undecidable("cannot read %s: %s" % (path, exc))


def _latch_names() -> list:
    """The latches the host currently holds."""
    return [name for name in ("tripped", "emergency") if (GUARD_STATE / name).exists()]


def _latch_identity(name: str) -> dict:
    """A latch's identity: its reason and its time.

    The identity is what a restore's evidence has to name exactly, so the
    detector reports it rather than only reporting that a latch exists.
    """
    row = _read_json(GUARD_STATE / name)
    if not isinstance(row, dict) or "at" not in row or "reason" not in row:
        raise Undecidable("latch %s is malformed" % name)
    return {"at": row["at"], "reason": row["reason"]}


def _monotonic() -> float:
    return time.monotonic()


def _guard_constant(name: str, fallback):
    """Read a constant from the installed guard, so the detector tracks the host.

    The thresholds are the guard's, not this file's.  If the installed guard
    changes them, the detector must follow, or it will report a gate the host
    does not use.
    """
    try:
        text = GUARD_SOURCE.read_text(encoding="utf-8")
    except OSError:
        return fallback
    match = re.search(r"^%s\s*=\s*([0-9.]+)" % re.escape(name), text, re.MULTILINE)
    if not match:
        return fallback
    try:
        return type(fallback)(match.group(1))
    except (TypeError, ValueError):
        return fallback


def _systemctl_show(unit: str, *props: str) -> dict:
    command = ["systemctl", "--user", "show", unit]
    for prop in props:
        command += ["-p", prop]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Undecidable("cannot read unit %s: %s" % (unit, exc))
    if result.returncode:
        raise Undecidable("systemctl show %s failed: %s" % (unit, (result.stderr or "")[-400:]))
    row = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            row[key.strip()] = value.strip()
    return row


def _unit_dropin_files(unit: str) -> list:
    """Every drop-in file that applies to a unit, with its directory.

    systemd searches the unit's own directory and the directories produced by
    truncating the unit name at each dash.  A file further down the hierarchy
    wins only when the names are equal; otherwise the names decide.
    """
    base = pathlib.Path.home() / ".config" / "systemd" / "user"
    candidates = []
    name = unit
    while True:
        candidates.append(base / (name + ".d"))
        if "-" not in name:
            break
        name = name.rsplit("-", 1)[0]
    found = []
    for directory in candidates:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.name.endswith(".conf"):
                found.append((directory.name, entry.name, entry))
    return found


def _effective_dropin_order(unit: str) -> list:
    """The order systemd applies a unit's drop-in files in.

    Files with different names are applied in lexicographic order by file
    name, regardless of directory.  Files with equal names are ordered by
    directory depth, deeper first.  The last one applied wins.
    """
    found = _unit_dropin_files(unit)
    depth = {name: index for index, (name, _, _) in enumerate(reversed(found))}
    ordered = []
    for directory_name, file_name, path in found:
        ordered.append((file_name, depth.get(directory_name, 0), directory_name, path))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return ordered


def _asks_for_lifeline_slice(unit: str) -> bool:
    """Whether any of the unit's drop-in files asks for the lifeline slice.

    A restore must not live in the plant slice: that is the slice the guard
    kills when it stops the workload.  When a drop-in asks for the lifeline
    slice and the effective slice is something else, the request was overridden.
    """
    for _file_name, _depth, _directory, path in _effective_dropin_order(unit):
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Slice=mishe-lifeline.slice" in text:
            return True
    return False


def _winning_dropins(unit: str, key: str) -> dict:
    """Which drop-in file's value of ``key`` is actually in force for a unit.

    For a scalar setting the last applied file that mentions it wins.  A file
    that clears the setting with an empty assignment resets it, so an empty
    value in a later file does not fall back to an earlier file's value.
    """
    winner = None
    for file_name, _, directory_name, path in _effective_dropin_order(unit):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        section = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() != key:
                continue
            if section not in ("Unit", "Service"):
                continue
            winner = dict(file=file_name, directory=directory_name, value=value.strip(),
                          path=str(path))
    return winner or {}


def assess(repo: pathlib.Path = REPO) -> dict:
    """Read the host and the repository and report the restore's readiness."""
    stable_seconds = _guard_constant("STABLE_SECONDS", STABLE_SECONDS)
    gate_io = _guard_constant("GATE_IO_PRESSURE", GATE_IO_PRESSURE)
    gate_mem = _guard_constant("GATE_MEMORY_PRESSURE", GATE_MEMORY_PRESSURE)

    config = _read_json(GUARD_CONFIG)
    status = _read_json(GUARD_RUN / "status.json")

    now_wall = time.time()
    age = now_wall - float(status.get("at", 0))
    if age < 0 or age > 20:
        raise Undecidable("sentinel observation is stale or future-dated (age %.1fs)" % age)

    observation = status.get("observation") or {}
    latches = {}
    for name in _latch_names():
        try:
            latches[name] = _latch_identity(name)
        except Undecidable as exc:
            raise Undecidable("latch %s is unreadable: %s" % (name, exc))

    # The continuous-healthy interval, measured the way the guard measures it.
    # clear_since is a time.monotonic() stamp, not elapsed seconds.
    clear_since = status.get("clear_since")
    interval = None
    if isinstance(clear_since, (int, float)) and not isinstance(clear_since, bool):
        interval = _monotonic() - float(clear_since)
        if interval < -1:
            interval = None

    blocking = []
    if observation.get("limits_ok") is not True:
        blocking.append("resource limits changed")
    if observation.get("ssh_available") is not True:
        blocking.append("SSH listener unavailable")
    if observation.get("host_io_available") is not True:
        blocking.append("host filesystem probe unavailable")
    io_pressure = observation.get("io_pressure")
    mem_pressure = observation.get("memory_pressure")
    if io_pressure is not None and io_pressure >= gate_io:
        blocking.append("io_pressure %.2f >= %s (the gate threshold, below the %s warning threshold)"
                        % (io_pressure, gate_io, WARNING_IO_PRESSURE))
    if mem_pressure is not None and mem_pressure >= gate_mem:
        blocking.append("memory_pressure %.2f >= %s" % (mem_pressure, gate_mem))
    maximum = observation.get("maximum") or 0
    if maximum and (observation.get("memory") or 0) >= maximum * 0.7:
        blocking.append("memory at or above 70%% of the maximum")
    task_max = observation.get("task_max") or 0
    if task_max and (observation.get("tasks") or 0) >= task_max * 0.7:
        blocking.append("tasks at or above 70%% of task_max")

    warning = bool(status.get("pressure_warning"))
    silent_band = bool(blocking) and not warning

    # The plant's own restore path.
    path_problems = []
    try:
        shown = _systemctl_show(RESTORE_UNIT, "Slice", "ConditionResult")
    except Undecidable as exc:
        path_problems.append("cannot inspect %s: %s" % (RESTORE_UNIT, exc))
        shown = {}
    slice_winner = _winning_dropins(RESTORE_UNIT, "Slice")
    condition_winner = _winning_dropins(RESTORE_UNIT, "ConditionPathExists")
    effective_slice = shown.get("Slice")
    condition_result = shown.get("ConditionResult")
    asked_lifeline = _asks_for_lifeline_slice(RESTORE_UNIT)
    if asked_lifeline and effective_slice and effective_slice != "mishe-lifeline.slice":
        path_problems.append(
            "the unit asks for Slice=mishe-lifeline.slice but the effective slice is %s; "
            "%s/%s is applied last" % (effective_slice, slice_winner.get("directory"),
                                       slice_winner.get("file")))
    if condition_winner.get("value", "").startswith("!/"):
        path_problems.append(
            "the effective ConditionPathExists=%s comes from %s/%s, so the restore unit is "
            "gated on the latch not existing and can never run"
            % (condition_winner.get("value"), condition_winner.get("directory"),
               condition_winner.get("file")))
    # ConditionResult is systemd's last evaluation of the unit's conditions.  It
    # is stale between evaluations: after a repair it keeps saying "no" until the
    # unit is started again.  So it is only evidence when it agrees with the
    # drop-in that is actually in force -- the drop-in is the durable fact.
    if condition_result == "no" and condition_winner.get("value", "").startswith("!/"):
        path_problems.append("systemd reports ConditionResult=no: the unit is skipped")

    tripped = "tripped" in latches
    if not tripped:
        state = "clear"
    elif blocking or interval is None or interval < stable_seconds:
        # The gate fails on a field that breaks it, on an interval that is not
        # being held at all, and on an interval shorter than the guard's
        # requirement.  Any of the three means a restore would be refused.
        state = "blocked"
    else:
        state = "ready"

    if tripped and blocking and silent_band:
        detail = ("latched, and the restore gate is failing in the silent band: %s. "
                  "No warning and no pressure receipt is recorded at these values."
                  % "; ".join(blocking))
    elif tripped and blocking:
        detail = "latched, and the restore gate is failing: %s" % "; ".join(blocking)
    elif tripped and interval is None:
        detail = ("latched, and no continuous healthy interval is being held, "
                  "so the required %ds cannot be satisfied" % stable_seconds)
    elif tripped and interval < stable_seconds:
        detail = ("latched, and the continuous healthy interval is %ds of the required %ds"
                  % (int(interval), stable_seconds))
    elif tripped:
        detail = ("latched, and the continuous healthy interval is %ds of the required %ds, "
                  "so a restore would pass its stability gate" % (int(interval), stable_seconds))
    else:
        detail = "no latch is present"

    if path_problems:
        detail += ". The plant's own restore path cannot run: " + "; ".join(path_problems)

    row = {
        "state": state,
        "detail": detail,
        "latch": latches.get("tripped"),
        "latches_present": sorted(latches),
        "emergency_latch": "emergency" in latches,
        "sentinel_age_seconds": round(age, 2),
        "sentinel_mode": status.get("mode"),
        "clear_since": clear_since,
        "continuous_healthy_seconds": None if interval is None else round(interval, 1),
        "stability_required_seconds": stable_seconds,
        "stability_satisfied": bool(interval is not None and interval >= stable_seconds),
        "blocking_fields": blocking,
        "pressure_warning": warning,
        "silent_band": silent_band,
        "io_pressure": io_pressure,
        "memory_pressure": mem_pressure,
        "gate_thresholds": {"io_pressure": gate_io, "memory_pressure": gate_mem},
        "warning_thresholds": {"io_pressure": WARNING_IO_PRESSURE,
                               "memory_pressure": WARNING_MEMORY_PRESSURE},
        "restore_unit": RESTORE_UNIT,
        "restore_unit_effective_slice": effective_slice,
        "restore_unit_condition_result": condition_result,
        "restore_unit_path_problems": path_problems,
        "restore_unit_slice_dropin": slice_winner,
        "restore_unit_condition_dropin": condition_winner,
        "life_support_state": (status.get("life_support") or {}).get("state"),
        "resume_units": len(config.get("resume_units") or []),
        "android_in_resume_units": [u for u in (config.get("resume_units") or [])
                                    if "android" in u.lower()],
    }
    if tripped and latches.get("tripped"):
        try:
            row["latch_sha256"] = hashlib.sha256(
                (GUARD_STATE / "tripped").read_bytes()).hexdigest()
        except OSError:
            pass
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Report whether the host's containment latch is clearable, and why not")
    parser.add_argument("--home", type=pathlib.Path, default=None,
                        help="site home; accepted for the observer-check runner contract")
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
        print("continuous healthy  : %s of %ss required"
              % (row["continuous_healthy_seconds"], row["stability_required_seconds"]))
        print("pressure warning    : %s" % row["pressure_warning"])
        print("restore unit slice  : %s (condition result %s)"
              % (row["restore_unit_effective_slice"], row["restore_unit_condition_result"]))
        for problem in row["restore_unit_path_problems"]:
            print("  path problem: %s" % problem)
    if args.state:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(row, indent=1, sort_keys=True), encoding="utf-8")
    if row["state"] in ("blocked",):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
