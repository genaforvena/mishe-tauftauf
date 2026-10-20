#!/usr/bin/env python3
"""Detector: the running observer's reading must equal the reviewed tree's reading.

On 2026-10-20 the plant's substrate observer ran a hand-copied import root
whose reader bytes predated the fix that corrected it. For 5 h 56 min every
role's wall carried ``SUBSTRATE: UNKNOWN`` and five faults the plant did not
have: the *reader* was wrong, not the plant. Nothing in the plant compared the
reading the observer publishes with the reading the reviewed tree would
publish, so the divergence stayed invisible for six hours.

``checks/observer-source-identity.py`` answers why the running bytes have no
commit identity — the observer's root is a copy by construction, so it reads
``mixed`` even while the plant's reading is right. This check answers the
other question, the one about the *effect*: does the running reader produce
the reviewed reading?

It answers it read-only, by computing the same observation twice — once with
the bytes the live observer runs, once with a clean export of the reviewed
HEAD tree — and comparing the two verdicts field by field. It also compares
the reading the plant has published with the reviewed one, so a published
reading that disagrees is caught even when the observer's bytes are current.

The plant's own runtime reading remains the authority: this check reports a
disagreement, not a verdict. A disagreement is also what an unreviewed
working tree legitimately produces.

Exit status:
  0  the readings agree
  2  a reading disagrees: the running observer's reading, or the published one
  3  the question cannot be decided: no import root, no repository, an
     unreadable module, a missing or stale published snapshot
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PACKAGE = "mishe_tauftauf"
SESSION_PREFIX = "mishe-self-development-current-"

# The parts of one reading that say what the plant looks like. Fields that
# only describe the sample (its own digest, its timestamp) are excluded: they
# differ between two samples of a plant that has not changed.
VERDICT_FIELDS = ("state", "selection_state", "source", "services_state",
                  "renderers_state", "problems", "service_states",
                  "renderer_states", "service_roots", "renderer_roots")

# The observation primitive is ``substrate_watch._runtime(home)``: the public
# ``check()`` writes monitoring state and sends DMs, and this check writes
# nothing. The child runs under one import root, so the two readings differ
# only in the bytes that produce them. The child rebuilds its own import path
# — the given root plus the standard library — and then checks where the
# package came from: an ambient installation, an editable install or a
# PYTHONPATH entry would otherwise answer for the observer's root, and the
# check would read agreement where there is none.
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

from mishe_tauftauf import substrate_watch

module = os.path.abspath(substrate_watch.__file__)
if not module.startswith(root + os.sep):
    raise SystemExit("the package came from " + module + ", not from " + root)

verdict = substrate_watch._runtime(Path(sys.argv[2]))
print(json.dumps({key: verdict.get(key) for key in sys.argv[3].split(",")}, sort_keys=True))
"""


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def unit_name(unit: str) -> str:
    """The full user-unit name, from a short name or a complete one."""
    return unit if unit.endswith(".service") else SESSION_PREFIX + unit + ".service"


def observer_root(unit: str) -> Path:
    """The import root the live observer unit runs the plant package from.

    The unit's own environment is the observation. A unit that is not loaded,
    names no import root, or names one that carries no plant package leaves
    the question undecided rather than green.
    """
    name = unit_name(unit)
    result = subprocess.run(["systemctl", "--user", "show", name, "-p", "Environment", "--value"],
                            capture_output=True, text=True)
    if result.returncode:
        raise Undecidable("unit not observable: " + name)
    for field in result.stdout.split():
        key, _, value = field.partition("=")
        if key != "PYTHONPATH":
            continue
        for part in value.split(":"):
            if part and (Path(part) / PACKAGE / "__init__.py").is_file():
                return Path(part)
    raise Undecidable("unit names no plant import root: " + name)


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


def read_verdict(root: Path, home: Path, cwd: Path) -> dict:
    """The reading the code under one import root produces on this plant."""
    result = subprocess.run([sys.executable, "-I", "-c", CHILD, str(root), str(home),
                             ",".join(VERDICT_FIELDS)],
                            capture_output=True, text=True, cwd=str(cwd))
    if result.returncode:
        raise Undecidable("the reader failed under " + str(root) + ": "
                          + result.stderr.strip()[-300:])
    try:
        verdict = json.loads(result.stdout)
    except ValueError:
        raise Undecidable("the reader under " + str(root) + " printed an unreadable verdict")
    if not isinstance(verdict, dict):
        raise Undecidable("the reader under " + str(root) + " printed no verdict")
    return verdict


def differences(observed: dict, reference: dict) -> list:
    """Field-level differences between two readings."""
    out = []
    for field in VERDICT_FIELDS:
        left, right = observed.get(field), reference.get(field)
        if left == right:
            continue
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                if left.get(key) != right.get(key):
                    out.append("%s[%s]: observed=%r reference=%r"
                               % (field, key, left.get(key), right.get(key)))
            continue
        if isinstance(left, list) and isinstance(right, list):
            for item in sorted(set(left) ^ set(right)):
                side = "observed-only" if item in left else "reference-only"
                out.append("%s: %s %r" % (field, side, item))
            continue
        out.append("%s: observed=%r reference=%r" % (field, left, right))
    return out


def published_runtime(home: Path) -> tuple:
    """The runtime reading the plant published, and when it published it."""
    path = home / "checks" / "substrate-watch.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Undecidable("no published substrate snapshot at " + str(path))
    except OSError as exc:
        raise Undecidable("the published substrate snapshot is unreadable: " + type(exc).__name__)
    try:
        row = json.loads(raw)
    except ValueError:
        raise Undecidable("the published substrate snapshot is malformed")
    runtime = row.get("runtime") if isinstance(row, dict) else None
    if not isinstance(runtime, dict):
        raise Undecidable("the published substrate snapshot carries no runtime reading")
    return runtime, row.get("checked_at")


def snapshot_age(checked_at) -> float:
    """Seconds since the published snapshot, or -1 when it carries no time."""
    if not isinstance(checked_at, str):
        return -1.0
    try:
        stamp = datetime.fromisoformat(checked_at)
    except ValueError:
        return -1.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).total_seconds()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--unit", default="substrate-watch",
                        help="unit whose reading is compared (default: substrate-watch)")
    parser.add_argument("--observer-src", type=Path, default=None,
                        help="import root the observer runs (default: read it from the unit)")
    parser.add_argument("--reference-src", type=Path, default=None,
                        help="import root of the reference tree (default: a clean HEAD export)")
    parser.add_argument("--max-age", type=float, default=300.0,
                        help="seconds after which the published snapshot counts as stale")
    args = parser.parse_args()
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    workdir = Path(tempfile.mkdtemp(prefix="observer-verdict-agreement-"))
    try:
        observer = args.observer_src.resolve() if args.observer_src else observer_root(args.unit)
        reference = (args.reference_src.resolve() if args.reference_src
                     else export_head(repo, workdir))
        observed = read_verdict(observer, home, workdir)
        expected = read_verdict(reference, home, workdir)
        observer_diffs = differences(observed, expected)
        publication_note = None
        publication_diffs = []
        try:
            published, checked_at = published_runtime(home)
            age = snapshot_age(checked_at)
            if age >= 0 and age > args.max_age:
                raise Undecidable("the published substrate snapshot is stale (age %.0fs)" % age)
            publication_diffs = differences(published, expected)
        except Undecidable as exc:
            publication_note = str(exc)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("observer  :", observer)
    print("reference :", reference)
    if publication_note is None:
        print("published :", checked_at, "(age %.0fs)" % max(age, 0.0))
    else:
        print("published : UNKNOWN — " + publication_note)

    if observer_diffs:
        print("observer  : DISAGREE — the running observer's reading differs "
              "from the reviewed tree's reading")
        for line in observer_diffs:
            print("    ", line)
    else:
        print("observer  : AGREE — the running observer's reading equals "
              "the reviewed tree's reading")
    if publication_note is None:
        if publication_diffs:
            print("published : DISAGREE — the published reading differs "
                  "from the reviewed tree's reading")
            for line in publication_diffs:
                print("    ", line)
        else:
            print("published : AGREE — the published reading equals "
                  "the reviewed tree's reading")
    if observer_diffs or publication_diffs:
        return 2
    if publication_note is not None:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
