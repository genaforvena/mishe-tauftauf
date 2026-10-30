#!/usr/bin/env python3
"""Detector: the host's guard bytes must be the bytes the repository names.

The gap this closes.  The plant's safety boundary is a file on the host,
``/usr/local/lib/mishe-containment/guard.py``, and the repository names the
bytes that file is supposed to carry in two pointer files under
``tools/guard-activation/commits/``.  ``tools/guard-activation/apply.py``
writes the file only when the installed bytes are exactly the bytes the
``revert_commit`` pointer names, and it refuses every write otherwise.

That refusal rule is the reason this detector exists.  On 2026-10-29 the
guard latched the whole plant off with the reason "protected recovery session
missing, dead or unobservable", and the cause was in the guard's own bytes: a
pane that exits between the tmux listing and the ``/proc/<pid>/cgroup`` read
makes one sample unusable while the session is live, and the installed guard
latched the plant off on that single sample.  The fix was already committed
(6cd0799) but had never reached the host, because the ``revert_commit``
pointer still named a commit the host did not carry, so every install was
refused.  The fix stayed off the host for a day and the plant stayed down.

Nothing watched for that class of failure.  ``apply.py`` and
``check-activation.py`` are run by hand, not on a timer, and no unit in the
plant reads the installed guard at all.  So an installed file that is not the
bytes the repository names is invisible until something trips, which is
exactly the failure this detector is for.

The detector answers one question, read-only:

  1. What are the bytes the repository names?  The install target and the
     revert target, read from the committed pointer files and from the
     commits they name.
  2. What are the bytes actually installed on the host?
  3. Do the running sentinel's bytes match the installed file, or is the
     installed file newer than the process that loads it?

Three distinct failures are reported separately, because they have different
consequences and different fixes:

* **drift** — the installed bytes are neither the install target nor the
  revert target.  Every write ``apply.py`` attempts will be refused, so a
  committed fix can never reach the host.  This is the failure that kept
  6cd0799 off the host for a day.
* **not activated** — the installed bytes are the install target, but the
  running sentinel started before they were written, so the boundary is
  still executing the previous code.  The fix is on disk and inert.
* **reverted** — the installed bytes are the revert target.  This is a
  legitimate state (the exact backout path exists), so it is reported as a
  state, not as a fault; the plant needs to know which boundary is live.

The repository is read for the committed pointers and the committed source
bytes; the host is read for the installed bytes and the running process's
start time.  Nothing is written and no plant program is imported.

Exit status:
  0  the installed bytes are the install target and the running sentinel
     loaded them, or the installed bytes are the revert target (a state, not
     a fault)
  2  the installed bytes are neither pointer's bytes, or the installed bytes
     are the install target but the running sentinel predates them
  3  the question cannot be decided: the repository, the pointer files, the
     named commits, the installed file or the running process cannot be read
     — an unreadable guard is UNKNOWN, never a pass and never a fault
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[1]
COMMITS = REPO / "tools" / "guard-activation" / "commits"
SOURCE = "src/mishe_tauftauf/host_containment.py"
GUARD_PATH = pathlib.Path("/usr/local/lib/mishe-containment/guard.py")
UNIT = "mishe-containment.service"


class Undecidable(Exception):
    """The checker lacks the evidence to decide, and says so."""


def _run(args: list[str], check: bool = True, timeout: int = 20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise Undecidable((result.stderr.strip() or result.stdout.strip()
                           or "command failed: " + " ".join(args[:3])))
    return result


def _pointer(name: str, repo: pathlib.Path = REPO) -> str:
    """The commit a pointer file names, read as bytes.

    The pointer is read as bytes rather than parsed, because ``apply.py``
    passes the file's bytes straight to ``git show``: a trailing newline
    would make the named commit unresolvable, and a pointer that names
    nothing is exactly the fault this detector reports.
    """
    path = repo / "tools" / "guard-activation" / "commits" / (name + ".commit")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Undecidable("cannot read the %s pointer: %s" % (name, exc))
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        raise Undecidable("the %s pointer file is empty" % name)
    return text


def _source_at(commit: str, repo: pathlib.Path = REPO) -> bytes:
    """The committed bytes of the guard source at a commit."""
    result = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (commit, SOURCE)],
                            capture_output=True)
    if result.returncode:
        raise Undecidable("cannot read %s at commit %s: %s"
                          % (SOURCE, commit, result.stderr.decode("utf-8", "replace").strip()))
    return result.stdout


def _installed_bytes() -> bytes:
    """The bytes on the host, read as root because the file is root-owned.

    An unreadable guard is UNKNOWN, not a fault and not a pass: a sudo
    failure is a privilege problem, and a verdict that said "drift" would
    send the operator to look for wrong bytes that are actually unreadable.
    """
    if not GUARD_PATH.exists():
        raise Undecidable("no guard is installed at %s" % GUARD_PATH)
    result = subprocess.run(["sudo", "-n", "cat", str(GUARD_PATH)], capture_output=True)
    if result.returncode:
        raise Undecidable("the installed guard could not be read as root "
                          "(sudo exit %d); every write will be refused" % result.returncode)
    if not result.stdout:
        raise Undecidable("the installed guard is empty; every write will be refused")
    return result.stdout


def _main_pid() -> int:
    result = _run(["systemctl", "show", UNIT, "-p", "MainPID", "--value"], check=False)
    if result.returncode:
        raise Undecidable("cannot read %s's main pid" % UNIT)
    try:
        pid = int(result.stdout.strip())
    except ValueError:
        raise Undecidable("%s reports no usable main pid" % UNIT)
    if pid <= 0:
        raise Undecidable("the sentinel unit has no running main process")
    return pid


def _process_started(pid: int) -> datetime:
    """When the running process began, from its own start time against boot time."""
    try:
        raw = pathlib.Path("/proc") / str(pid) / "stat"
        fields = raw.read_text().rsplit(") ", 1)[1].split()
        start_ticks = int(fields[19])
        boot_line = next(row for row in pathlib.Path("/proc/stat").read_text().splitlines()
                         if row.startswith("btime "))
        boot_time = int(boot_line.split()[1])
        ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        raise Undecidable("the running process's start time is unreadable")
    return datetime.fromtimestamp(boot_time + start_ticks / ticks, timezone.utc)


def _installed_written() -> datetime:
    try:
        return datetime.fromtimestamp(GUARD_PATH.stat().st_mtime, timezone.utc)
    except OSError as exc:
        raise Undecidable("the installed guard's write time is unreadable: %s" % exc)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assess(repo: pathlib.Path = REPO) -> dict:
    """The installed bytes against the repository's named bytes.

    Returns a row whose ``state`` is one of ``drift``, ``not-activated``,
    ``reverted`` or ``current``.  Raises ``Undecidable`` when the evidence
    cannot be read, so a missing guard never reads as a fault.
    """
    install_commit = _pointer("install_commit", repo)
    revert_commit = _pointer("revert_commit", repo)
    install_bytes = _source_at(install_commit, repo)
    revert_bytes = _source_at(revert_commit, repo)
    installed = _installed_bytes()
    row = {
        "install_commit": install_commit,
        "revert_commit": revert_commit,
        "install_sha256": _digest(install_bytes),
        "revert_sha256": _digest(revert_bytes),
        "installed_sha256": _digest(installed),
    }
    if installed == install_bytes:
        pid = _main_pid()
        started = _process_started(pid)
        written = _installed_written()
        row["running_pid"] = pid
        row["running_started_utc"] = started.isoformat()
        row["installed_written_utc"] = written.isoformat()
        if started < written:
            row["state"] = "not-activated"
            row["detail"] = ("the installed guard is the install target, but the running "
                             "sentinel started before those bytes were written; it is "
                             "still executing the previous code")
        else:
            row["state"] = "current"
            row["detail"] = "the installed guard is the install target and the running sentinel loaded it"
        return row
    if installed == revert_bytes:
        row["state"] = "reverted"
        row["detail"] = ("the installed guard is the revert target; the plant is running the "
                         "pre-fix boundary, which is a state and not a fault")
        return row
    row["state"] = "drift"
    row["detail"] = ("the installed guard matches neither pointer; every write will be refused, "
                     "so a committed fix for the trip cause can never reach the host")
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Detect drift between the host's guard bytes and the repository's named bytes")
    parser.add_argument("--home", type=pathlib.Path, default=None,
                        help="site home; accepted for the observer-check runner contract")
    parser.add_argument("--repo", type=pathlib.Path, default=REPO,
                        help="reviewed repository holding the guard pointers and source")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    parser.add_argument("--state", type=pathlib.Path, default=None,
                        help="write the verdict to this JSON file")
    args = parser.parse_args(argv)
    try:
        row = assess(args.repo)
    except Undecidable as exc:
        unknown = {"state": "UNKNOWN", "reason": str(exc)}
        # The JSON form is machine-readable in both cases: a monitor reading
        # this check's output must not have to parse prose to learn whether
        # the question was decided.
        if args.json:
            print(json.dumps(unknown, indent=1))
        else:
            print("UNKNOWN — " + str(exc))
        if args.state:
            args.state.parent.mkdir(parents=True, exist_ok=True)
            args.state.write_text(json.dumps(unknown, indent=1), encoding="utf-8")
        return 3
    if args.json:
        print(json.dumps(row, indent=1))
    else:
        print("%s: %s" % (row["state"].upper(), row["detail"]))
        print("installed sha256:", row["installed_sha256"])
        print("install target  :", row["install_sha256"], "(" + row["install_commit"] + ")")
        print("revert target  :", row["revert_sha256"], "(" + row["revert_commit"] + ")")
        if row.get("running_pid"):
            print("running pid     :", row["running_pid"], row.get("running_started_utc", ""))
    if args.state:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(row, indent=1), encoding="utf-8")
    if row["state"] in ("drift", "not-activated"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
