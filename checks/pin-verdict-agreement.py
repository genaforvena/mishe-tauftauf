#!/usr/bin/env python3
"""Detector: the verdict the plant prints must be the verdict the reviewed tree computes.

``checks/observer-view-agreement.py`` compares the *evidence block* every role's
pane shows with the projections the reviewed tree publishes. It finds a
projection the reviewed tree has and the plant does not print — a missing
observation.

This check asks the question at the *verdict* level, and it asks it about a
verdict that the plant prints today as RED. On 2026-10-20 the plant's dashboard
read:

    RUNTIME: pin=... DRIFT — undeclared: mishe-self-development-current-follow-through-watch.service,
    mishe-self-development-current-initiative.service,mishe-self-development-current-substrate-watch.service
    STATE: RED — runtime drift: service import roots differ from the pinned release

Health committed a decision record on the same day,
``health/declared-roots.json`` (55d14de): those three units import the observer
bundle on purpose, because the pinned release carries no ``substrate_watch``,
``initiative`` or ``follow_through`` module. The reviewed tree honours that
record, so on the same live service roots it reads ``DECLARED`` and the state
line reads GREEN. The pinned bytes predate the record: no module the pin
carries names ``declared-roots.json`` at all, so the declaration cannot take
effect and the plant prints a fault the site has already resolved.

A missing observation and a phantom fault are the two directions of one class:
running bytes that predate the reviewed tree make the plant's reading wrong in
both directions. This check detects the direction in which the plant prints a
*fault* the reviewed tree does not.

The comparison is over one capture. The plant's own capture of the live
service roots is taken with the pinned bytes — the bytes the plant itself
runs — and the same capture is then judged by the pinned bytes and by a clean
export of the reviewed HEAD tree. The plant's printed line is compared with
the pinned bytes' own verdict first: a printed line the pinned bytes cannot
reproduce is *undecidable*, not a disagreement, because the plant may have
printed it under different conditions.

``checks/observer-view-agreement.py`` covers the evidence block; this check
covers the verdict line. Neither covers the other.

Exit status:
  0  the plant's printed verdict is the verdict the reviewed tree computes
  2  the plant prints a verdict the reviewed tree contradicts — a phantom fault
  3  the question cannot be decided: no published frame, an unreadable or
     malformed frame, no pin, no reviewed tree, or an unreadable module

The verdict is the last line of the output; the lines before it say what was
compared. A runner may read the exit status alone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = "mishe_tauftauf"
ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RUNTIME_RE = re.compile(r"^RUNTIME: .*? (MATCH|DECLARED|DRIFT|UNKNOWN|NONE|UNPINNED)\b")
STATE_RE = re.compile(r"^STATE: (.*)$")

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

from mishe_tauftauf import wall_view

module = os.path.abspath(wall_view.__file__)
if not module.startswith(root + os.sep):
    raise SystemExit("the package came from " + module + ", not from " + root)

home = Path(sys.argv[2])
mode = sys.argv[3]
if mode == "capture":
    lines, roots, note = wall_view._service_block(home)
    print(json.dumps({"roots": roots, "note": note, "lines": lines}))
else:
    roots = json.loads(sys.argv[4])
    state, line = wall_view._runtime_state(home, Path(root).parent, roots)
    print(json.dumps({"state": state, "line": line}))
"""


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def published_frame(home: Path, slug: str) -> tuple:
    """The frame the plant published for one role, and the verdict lines in it."""
    path = home / "dashboards" / f"{slug}.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Undecidable("no published dashboard frame at " + str(path))
    except OSError as exc:
        raise Undecidable("the published dashboard frame is unreadable: " + type(exc).__name__)
    try:
        record = json.loads(raw)
    except ValueError:
        raise Undecidable("the published dashboard frame is malformed")
    if not isinstance(record, dict) or not isinstance(record.get("frame"), str):
        raise Undecidable("the published dashboard frame carries no text")
    if record.get("slug") != slug:
        raise Undecidable("the published dashboard frame names another role")
    digest = record.get("sha256")
    if isinstance(digest, str) and hashlib.sha256(record["frame"].encode()).hexdigest() != digest:
        raise Undecidable("the published dashboard frame's digest does not match its text")
    runtime = None
    state = None
    for line in record["frame"].splitlines():
        match = RUNTIME_RE.match(line)
        if match and runtime is None:
            runtime = (match.group(1), line)
        match = STATE_RE.match(line)
        if match and state is None:
            state = (match.group(1), line)
    if runtime is None or state is None:
        raise Undecidable("the published dashboard frame carries no runtime verdict")
    return runtime, state, record.get("created")


def pin_root(home: Path) -> Path:
    """The import root of the bytes the plant is pinned to."""
    path = home / "health" / "runtime-release.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Undecidable("no runtime pin record at " + str(path))
    except (OSError, ValueError) as exc:
        raise Undecidable("the runtime pin record is unreadable: " + type(exc).__name__)
    if not isinstance(record, dict) or not isinstance(record.get("source"), str):
        raise Undecidable("the runtime pin record names no source")
    root = Path(record["source"]) / "src"
    if not (root / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the pinned source carries no plant package")
    return root


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


def run_child(root: Path, home: Path, cwd: Path, mode: str, roots_json: str = "null") -> dict:
    """Ask the code under one import root what it says about the live plant."""
    result = subprocess.run([sys.executable, "-I", "-c", CHILD, str(root), str(home), mode, roots_json],
                            capture_output=True, text=True, cwd=str(cwd))
    if result.returncode:
        raise Undecidable("the verdict failed under " + str(root) + ": "
                          + result.stderr.strip()[-300:])
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise Undecidable("the verdict under " + str(root) + " printed no readable answer")
    if not isinstance(payload, dict):
        raise Undecidable("the verdict under " + str(root) + " printed no readable answer")
    return payload


def unreachable_records(home: Path, pin: Path, repo: Path) -> list:
    """Site decision records the pinned bytes cannot read.

    A record is reachable when some module the pin carries names it. The
    reviewed tree's modules are searched for the same name, so a record the
    tree reads and the pin does not is named as the cause of a disagreement.
    """
    directory = home / "health"
    if not directory.is_dir():
        return []
    pin_sources = list((pin / PACKAGE).glob("*.py"))
    tree_sources = list((repo / "src" / PACKAGE).glob("*.py"))
    out = []
    for record in sorted(directory.glob("*.json")):
        name = record.name
        in_pin = any(name in source.read_text(encoding="utf-8", errors="replace")
                     for source in pin_sources)
        in_tree = any(name in source.read_text(encoding="utf-8", errors="replace")
                      for source in tree_sources)
        if in_tree and not in_pin:
            out.append("health/" + name)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--slug", required=True, help="role whose published verdict is compared")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--reference-src", type=Path, default=None,
                        help="import root of the reference tree (default: a clean HEAD export)")
    args = parser.parse_args()
    if not ROLE_RE.match(args.slug):
        print("UNKNOWN — the role name is not a valid slug")
        return 3
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    workdir = Path(tempfile.mkdtemp(prefix="pin-verdict-agreement-"))
    try:
        reference = (args.reference_src.resolve() if args.reference_src
                     else export_head(repo, workdir))
        pin = pin_root(home)
        runtime, state, created = published_frame(home, args.slug)
        captured = run_child(pin, home, workdir, "capture")
        if captured.get("roots") is None:
            raise Undecidable("the plant's own capture of the service roots is unreadable: "
                              + str(captured.get("note")))
        roots_json = json.dumps(captured["roots"])
        pinned = run_child(pin, home, workdir, "judge", roots_json)
        expected = run_child(reference, home, workdir, "judge", roots_json)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("published  :", home / "dashboards" / (args.slug + ".json"), "at", created)
    print("pin        :", pin)
    print("reference  :", reference)
    print("plant line :", runtime[1])
    print("plant state:", state[1])
    print("pinned verdict on the same capture:", pinned.get("state"), "-", pinned.get("line"))
    print("reviewed verdict on the same capture:", expected.get("state"), "-", expected.get("line"))

    if pinned.get("state") != runtime[0]:
        print("UNKNOWN — the pinned bytes do not reproduce the plant's printed verdict "
              "(" + str(runtime[0]) + " printed, " + str(pinned.get("state")) + " computed); "
              "the plant printed it under conditions this capture does not reproduce")
        return 3
    if expected.get("state") == pinned.get("state"):
        print("verdict    : AGREE — the plant's printed verdict is the verdict the "
              "reviewed tree computes")
        return 0
    print("verdict    : DISAGREE — the plant prints a verdict the reviewed tree contradicts")
    records = unreachable_records(home, pin, repo)
    if records:
        print("cause      : the pinned bytes cannot read the site's decision record(s): "
              + ", ".join(records))
        print("            no module the pin carries names them; the reviewed tree does")
    return 2


if __name__ == "__main__":
    sys.exit(main())
