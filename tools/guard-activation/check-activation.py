#!/usr/bin/env python3
"""Report whether the running sentinel actually loaded the installed guard bytes.

Installing a file is not activating it.  The sentinel is a long-running Python
process: it loads its code once, at exec time, and keeps it.  Replacing
``guard.py`` on disk therefore leaves the running boundary comparing limits
with whatever it loaded at start, which is how an installed fix can sit
untested while the plant is latched off by the old behaviour.

The verdict is deterministic and comes from the process's own start time:

* a process that started BEFORE the installed file was last written cannot
  have loaded those bytes, because they did not exist when it started;
* a process that started AT OR AFTER that moment did load them, because the
  unit's ``ExecStart`` reads that exact path.

The running bytes cannot be hashed from outside: the interpreter closes the
source file after reading it, so the process holds no descriptor to it.  The
start-time comparison is the available ground truth, and it is the direction
that matters -- it cannot report "activated" for a process that predates the
file.

Usage:
  python3 check-activation.py            # report, exit 0 only when activated
  python3 check-activation.py --json     # machine-readable report
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys

GUARD_PATH = pathlib.Path("/usr/local/lib/mishe-containment/guard.py")
UNIT = "mishe-containment.service"
UNIT_EXECSTART = "/usr/local/lib/mishe-containment/guard.py"


def _run(command: list[str]) -> tuple[int, str]:
    proc = subprocess.run(command, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def _sha256(path: pathlib.Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _unit_property(name: str) -> str | None:
    code, out = _run(["systemctl", "show", UNIT, "-p", name, "--value"])
    return out if code == 0 and out else None


def _main_pid() -> int | None:
    try:
        pid = int(_unit_property("MainPID") or "")
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_started(pid: int) -> datetime.datetime | None:
    """When the process began, from its own start time against the boot time."""
    try:
        raw = pathlib.Path("/proc") / str(pid) / "stat"
        fields = raw.read_text().rsplit(") ", 1)[1].split()
        start_ticks = int(fields[19])
        boot_line = next(row for row in pathlib.Path("/proc/stat").read_text().splitlines()
                         if row.startswith("btime "))
        boot_time = int(boot_line.split()[1])
        ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    return datetime.datetime.fromtimestamp(boot_time + start_ticks / ticks, datetime.UTC)


def _exec_start_path() -> str | None:
    """The script the unit actually runs, read from the unit itself."""
    code, out = _run(["systemctl", "show", UNIT, "-p", "ExecStart", "--value"])
    if code != 0 or not out:
        return None
    # ExecStart renders as a bracketed argv list.
    for token in out.replace(";", " ").replace("{", " ").replace("}", " ").split():
        if token.endswith("guard.py"):
            return token.strip("'\"")
    return None


def report() -> dict:
    installed_digest = _sha256(GUARD_PATH)
    try:
        installed_mtime = datetime.datetime.fromtimestamp(
            pathlib.Path(GUARD_PATH).stat().st_mtime, datetime.UTC)
    except OSError:
        installed_mtime = None
    pid = _main_pid()
    row = dict(
        unit=UNIT,
        guard_path=str(GUARD_PATH),
        installed_sha256=installed_digest,
        installed_written_utc=installed_mtime.isoformat() if installed_mtime else None,
        running_pid=pid,
        running_started_utc=None,
        exec_start=UNIT_EXECSTART,
        activated=None,
        reason=None,
    )
    if installed_digest is None:
        row["reason"] = "the installed guard file is missing or unreadable"
        return row
    if pid is None:
        row["reason"] = "the sentinel unit has no running main process"
        return row
    started = _process_started(pid)
    if started is None:
        row["reason"] = "the running process's start time is unreadable"
        return row
    row["running_started_utc"] = started.isoformat()
    if installed_mtime is None:
        row["reason"] = "the installed guard file's write time is unreadable"
        return row
    if started < installed_mtime:
        row["reason"] = ("the running sentinel started before the installed bytes were written; "
                         "it is still executing the previous code")
        return row
    row["activated"] = True
    row["reason"] = "the running sentinel started after the installed bytes were written"
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    row = report()
    if args.json:
        print(json.dumps(row, indent=1))
    else:
        print("unit             :", row["unit"])
        print("installed        :", row["installed_sha256"], row["installed_written_utc"] or "")
        print("running pid      :", row["running_pid"], row["running_started_utc"] or "")
        print("activated        :", row["activated"])
        print("reason           :", row["reason"])
        if row["activated"] is not True:
            print("next action      : restart the sentinel, then confirm this check reports activated")
    return 0 if row["activated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
