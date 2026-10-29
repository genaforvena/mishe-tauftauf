#!/usr/bin/env python3
"""The attempts of a pending native wake that no plant record names.

The plant's dispatch guard consults two records about a native role: the busy
marker (an attempt is in progress) and the hold record (a wake cannot proceed).
When neither exists, a retained attempt of the role's pending wake is invisible
to the plant: the readiness gate reports the role ready and cannot tell that
the wake it is about to deliver already has an attempt. The resident derives its
own decision from the attempt's bytes when it runs, so the delivery is not
misrouted by this; what the plant lacks is the evidence.

This check reads that fact from the plant's own bytes and decides it with the
reviewed tree's rule. ``native_recovery.unseen_attempts`` owns which
directories belong to a wake and which plant records name an attempt, and
``native_recovery.attempt_disposition`` owns the bounded next action, so this
check does not re-derive the boundary. The tree it decides with is a clean
export of HEAD, not the working tree's bytes and not the plant's pin, so an
unreviewed change cannot make the check agree with itself and a stale pin
cannot make it disagree.

Where a role's attempts live is read from the plant's own layout: a resident
keeps a role's attempts in a ``wakes`` directory whose parent names the role
(``home/native/<role>/wakes`` in the tracked layout, ``home/<canary>/<role>/wakes``
in the canary layout the plant runs). A parent that names no role is not a
resident's attempt directory and is not read.

Exit codes:
  0  no role has an attempt of its pending wake that no plant record names
  2  at least one role does; the findings are printed
  3  the plant cannot be read, or a native role's attempts cannot be located:
     the reading is UNKNOWN, which is not a clean reading

A finding outranks an unknown reading: the finding is actionable, and the
unknown readings are printed with it.

The limits of the claim, because a reader of the plant will act on it:

- The role's branch comes from the plant's own readiness record. The plant
  rewrites that record when the verdict changes, and the branch is part of the
  verdict, so a record that names a branch names the branch the plant last
  saw. A plant whose readiness loop has died keeps a stale record; the
  attempt directories are read from the plant's own layout regardless of the
  branch, so only a role whose attempts live outside a `wakes` directory can
  be missed by a stale branch.
- The check reads the plant's records. It does not deliver, hold or release
  anything, and it does not say that a delivery will happen: a reported
  attempt is a fact about the plant's records.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = "mishe_tauftauf"
ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# The resident's own convention: a role's attempts live in a `wakes` directory
# whose parent names the role. The reviewed native resident builds that path
# from `config.directory / 'wakes'`, and `config.directory` is
# `home / 'native' / role`.
WAKE_DIR = "wakes"

# Directories that cannot hold a resident's live attempts. The site home keeps
# releases, worktrees and snapshots: those are copies of the tree, and a copy
# of an attempt is not an attempt.
SKIP_DIRS = frozenset({
    "releases", "worktrees", "snapshots", "retired-candidates", "artifacts",
    "records", "experiments", "research", "chat-out", "dm-outbox", "outbox",
    "backups", "ops", "omp-recovery", ".git",
})

# The walk is bounded in depth as well as in names: the plant's own layout
# holds a role's attempts a few levels under the home, and a deeper tree is a
# copy or an archive.
MAX_DEPTH = 6

# The child rebuilds its own import path: the given root plus the standard
# library. An ambient installation, an editable install or a PYTHONPATH entry
# would otherwise answer for the root under test, and the check would read
# agreement where there is none.
CHILD = r"""
import json
import os
import sys
import sysconfig
from pathlib import Path

root = os.path.abspath(sys.argv[1])
stdlib = {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["platstdlib"]}
keep = []
for entry in sys.path:
    if not entry:
        continue
    resolved = os.path.abspath(entry)
    if any(resolved == base or resolved.startswith(base + os.sep) for base in stdlib):
        keep.append(entry)
sys.path = [root] + keep

from mishe_tauftauf import activity, native_recovery as recovery
from mishe_tauftauf.feed import Feed

module = os.path.abspath(recovery.__file__)
if not module.startswith(root + os.sep):
    raise SystemExit("the package came from " + module + ", not from " + root)

home = Path(sys.argv[2])
wake_dir = sys.argv[3]
skip = set(json.loads(sys.argv[4]))
max_depth = int(sys.argv[5])
extra_roots = json.loads(sys.argv[6])


def walk_wake_roots():
    # Every `wakes` directory under the home, with the role its parent names.
    found = []
    for base, dirs, _files in os.walk(home, onerror=lambda exc: None):
        rel = os.path.relpath(base, home)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = [name for name in dirs
                   if name not in skip and not name.startswith(".")]
        if depth >= max_depth:
            dirs[:] = []
            continue
        if os.path.basename(base) == wake_dir:
            found.append(base)
            dirs[:] = []
    return found


def gate_record(role):
    # The plant's own readiness record for the role, as it stands.
    path = home / "checks" / ("native-gate-" + role + ".json")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    return {key: record.get(key) for key in
            ("at", "branch", "engine", "verdict", "busy", "blocked", "reason")}


def attempts_of(root, wake):
    # The attempt directories of a wake, as the reviewed boundary names them.
    return [path.name for path in recovery.wake_attempts(Path(root), wake)]


roots = {}
for base in walk_wake_roots():
    parent = os.path.basename(os.path.dirname(base))
    roots.setdefault(parent, []).append(base)

result = {"roles": [], "errors": []}
# A plant with no tape has no pending wake and no settled one, so the reading
# would be empty and clean. An absent tape is an absent observation, not a
# clean one, so it is reported before any role is read.
tape = Feed(home).path
if not tape.is_file():
    result["errors"].append("the plant's tape is absent at " + str(tape))
try:
    roles = sorted(activity.roles(home))
except Exception as exc:  # noqa: BLE001 - the reason is reported, not guessed
    result["errors"].append("the role registry is unreadable: "
                             + type(exc).__name__ + ": " + str(exc))
    roles = []

for role in roles:
    entry = {"role": role, "pending": None, "roots": [], "busy": None, "hold": None,
             "gate": gate_record(role), "errors": []}
    result["roles"].append(entry)
    try:
        pending = recovery.pending_wake(home, role)
    except Exception as exc:  # noqa: BLE001 - a broken tape is not a clean reading
        entry["errors"].append("the plant's tape cannot be read: "
                               + type(exc).__name__ + ": " + str(exc))
        continue
    entry["pending"] = pending
    if pending is None:
        continue
    try:
        entry["busy"] = recovery.busy_path(home, role).exists()
        blocked, reason = recovery.read_blocked_state(home, role)
        entry["hold"] = {"present": blocked is not None, "reason": reason,
                         "record": blocked}
    except Exception as exc:  # noqa: BLE001 - reported, not guessed
        entry["errors"].append("the plant's records for the role cannot be read: "
                                + type(exc).__name__ + ": " + str(exc))
    candidates = list(roots.get(role, []))
    for extra in extra_roots:
        if extra not in candidates:
            candidates.append(extra)
    for base in candidates:
        path = Path(base)
        row = {"path": str(path), "exists": path.is_dir(), "attempts": [],
               "unseen": [], "disposition": None}
        entry["roots"].append(row)
        if not path.is_dir():
            continue
        try:
            row["attempts"] = attempts_of(path, pending)
            for item in recovery.unseen_attempts(home, role, path, pending):
                row["unseen"].append(item.to_record())
            row["disposition"] = recovery.attempt_disposition(path, pending).to_record()
        except Exception as exc:  # noqa: BLE001 - reported, not guessed
            entry["errors"].append("the attempts of the role cannot be read: "
                                    + type(exc).__name__ + ": " + str(exc))

print(json.dumps(result))
"""


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def export_head(repo: Path, workdir: Path) -> Path:
    """A clean export of the reviewed HEAD tree.

    The reference is the reviewed tree and not the working tree's uncommitted
    bytes, so an unreviewed change cannot make the check agree by itself.
    """
    archive = subprocess.run(["git", "-C", str(repo), "archive", "HEAD", "src"],
                             capture_output=True)
    if archive.returncode:
        raise Undecidable("cannot export HEAD from " + str(repo))
    extract = subprocess.run(["tar", "-x", "-C", str(workdir)], input=archive.stdout,
                             capture_output=True)
    if extract.returncode:
        raise Undecidable("cannot unpack the HEAD export")
    root = workdir / "src"
    if not (root / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the HEAD export carries no plant package")
    return root


def read_roles(root: Path, home: Path, cwd: Path, extra_roots: list) -> dict:
    """The plant's roles and their records, read with the reviewed tree."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", CHILD, str(root), str(home), WAKE_DIR,
         json.dumps(sorted(SKIP_DIRS)), str(MAX_DEPTH), json.dumps(extra_roots)],
        capture_output=True, text=True, cwd=str(cwd))
    if result.returncode:
        raise Undecidable("the reading failed under " + str(root) + ": "
                          + result.stderr.strip()[-300:])
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise Undecidable("the reading under " + str(root) + " printed an unreadable report")
    if not isinstance(report, dict) or not isinstance(report.get("roles"), list):
        raise Undecidable("the reading under " + str(root) + " printed no report")
    return report


def branch_of(gate) -> str:
    """The plant's own reading of the role's branch, or an empty string."""
    if not isinstance(gate, dict):
        return ""
    branch = gate.get("branch")
    return branch if isinstance(branch, str) else ""


def render(home: Path, root: Path, report: dict) -> int:
    """Print the reading and return the check's exit code."""
    print("plant     :", home)
    print("reference :", root)
    findings = 0
    unknown = 0
    for error in report.get("errors", []):
        print("UNKNOWN   :", error)
        unknown += 1
    for entry in report["roles"]:
        role = entry["role"]
        pending = entry.get("pending")
        errors = entry.get("errors") or []
        for error in errors:
            print("UNKNOWN   :", role, "-", error)
            unknown += 1
        if pending is None:
            continue
        gate = entry.get("gate") or {}
        branch = branch_of(gate)
        print("role      :", role, "- pending wake", pending,
              "- branch", branch or "unknown")
        if gate:
            print("  gate    :", gate.get("verdict") or "no verdict",
                  "at", gate.get("at") or "no time",
                  "(engine", gate.get("engine") or "unknown", ")")
        hold = entry.get("hold") or {}
        print("  records :", "busy", "present" if entry.get("busy") else "absent",
              "- hold", "present" if hold.get("present") else
              ("unreadable" if hold.get("reason") else "absent"))
        if hold.get("reason"):
            # A hold record that cannot be decoded leaves the plant's reading
            # UNKNOWN, so the attempt it would have named is not an unnamed
            # attempt, and the reading is not a clean one either.
            print("  hold    : UNKNOWN - the hold record cannot be decoded:",
                  hold.get("reason"))
            unknown += 1
        roots = entry.get("roots") or []
        if not roots:
            if branch == "delegation" or not branch:
                print("  attempts: UNKNOWN - this role's attempts cannot be located, "
                      "so a retained attempt cannot be ruled out")
                unknown += 1
            else:
                print("  attempts: none - a", branch, "role writes no attempt directory")
            continue
        for row in roots:
            print("  attempts:", row["path"], "-", len(row.get("attempts") or []),
                  "retained for this wake")
            for item in row.get("unseen") or []:
                print("    UNSEEN:", os.path.basename(item["attempt"]),
                      "-", item.get("verdict"), "-", item.get("reason"))
                findings += 1
            disposition = row.get("disposition") or {}
            action = disposition.get("action")
            if action and action != "start":
                print("    next   :", action, "-", disposition.get("reason"))
    if findings:
        print("VERDICT   : FAIL -", findings, "attempt(s) of a pending wake that no "
              "plant record names")
        return 2
    if unknown:
        print("VERDICT   : UNKNOWN -", unknown, "reading(s) the plant cannot supply")
        return 3
    print("VERDICT   : OK - every pending wake's attempts are named by a plant record")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--reference-src", type=Path, default=None,
                        help="import root of the reference tree (default: a clean HEAD export)")
    parser.add_argument("--role", action="append", default=[],
                        help="only report this role (repeatable)")
    parser.add_argument("--attempts-root", action="append", default=[],
                        help="an attempts directory to read in addition to the plant's own")
    args = parser.parse_args()
    for role in args.role:
        if not ROLE_RE.match(role):
            print("UNKNOWN - the role name is not a valid slug:", role)
            return 3
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    extra_roots = [str(Path(path).resolve()) for path in args.attempts_root]
    workdir = Path(tempfile.mkdtemp(prefix="native-unseen-attempt-"))
    try:
        reference = (args.reference_src.resolve() if args.reference_src
                     else export_head(repo, workdir))
        report = read_roles(reference, home, workdir, extra_roots)
    except Undecidable as exc:
        print("UNKNOWN -", str(exc))
        return 3
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if args.role:
        report["roles"] = [entry for entry in report["roles"] if entry["role"] in args.role]
    return render(home, reference, report)


if __name__ == "__main__":
    sys.exit(main())
