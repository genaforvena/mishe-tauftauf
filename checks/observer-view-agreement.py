#!/usr/bin/env python3
"""Detector: the evidence the plant prints must be the evidence the tree publishes.

``checks/observer-verdict-agreement.py`` compares the *substrate verdict*: the
state, the problems and the roots of one reading. On 2026-10-20 that verdict
agreed — both sides said ``KNOWN`` — while the plant still printed an evidence
block that the reviewed tree would not print. The block the plant shows every
role is produced by ``mishe_tauftauf.follow_through`` running under the
observer bundle's import root, and that bundle is a hand copy of the reviewed
tree taken at 16:40Z. It is refreshed one file at a time, when a reading is
observed to be wrong; nothing refreshes it when a *new* projection is added.

Commit 49bb564 (2026-10-20 20:22Z) added the outcome-refusal projection to
``follow_through.py``. The bundle's copy predates it, so the plant printed no
refusal record while the reviewed tree reported 13 refused outcome publications
in the last 24 hours. A reader of the plant could not tell "no refusals" from
"this projection is not there": an absent observation reads as a clean one.

This check asks the effect-level question, on the plant's own output: does the
block the plant publishes contain the projections the reviewed tree publishes?
A projection the reviewed tree has and the plant does not is a *missing
observation*, never a healthy reading.

It compares the published frame — the bytes every role's pane shows — with a
clean export of the reviewed HEAD tree, and reports the projections the plant
does not print, verbatim, so the content that is being hidden is visible.

``checks/observer-source-identity.py`` answers why the running bytes have no
commit identity; this check answers what the difference costs.

Exit status:
  0  the plant's published evidence carries the reviewed tree's projections
  2  a projection is missing from the plant's published evidence, or the plant
     prints one the reviewed tree does not
  3  the question cannot be decided: no published frame, an unreadable or
     malformed frame, no reviewed tree, or an unreadable module
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
SECTION = "MIND WALL — shared follow-through evidence"
ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PYTHONPATH_RE = re.compile(r"PYTHONPATH=['\"]?([^'\"\s:]+)")

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

from mishe_tauftauf import follow_through

module = os.path.abspath(follow_through.__file__)
if not module.startswith(root + os.sep):
    raise SystemExit("the package came from " + module + ", not from " + root)

print(json.dumps(follow_through.lines(Path(sys.argv[2]))))
"""


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def label(line: str) -> str:
    """The projection a line carries, without its value.

    Two samples of a plant that has not changed differ in timestamps, digests
    and counters. What must not differ is *which* observations are present, so
    the comparison is over the labels.
    """
    text = line.strip()
    if not text:
        return ""
    colon = text.find(":")
    equals = text.find("=")
    if colon != -1 and (equals == -1 or colon < equals):
        return text[:colon].strip()
    if equals != -1:
        return text[:equals].strip()
    return text.split(None, 1)[0].strip()


def projections(lines) -> dict:
    """The projections of a block, keyed by label, in first-seen order."""
    out = {}
    for line in lines:
        if not isinstance(line, str):
            continue
        key = label(line)
        if not key:
            continue
        out.setdefault(key, line)
    return out


def published_section(home: Path, slug: str) -> tuple:
    """The evidence block the plant published for one role, and its time."""
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
    frame = record["frame"]
    lines = frame.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(SECTION):
            start = index + 1
            break
    if start is None:
        raise Undecidable("the published dashboard frame carries no evidence block")
    body = []
    for line in lines[start:]:
        if line.startswith("MIND WALL —"):
            break
        if line.strip():
            body.append(line)
    if not body:
        raise Undecidable("the published evidence block is empty")
    return body, record.get("created")


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


def read_lines(root: Path, home: Path, cwd: Path) -> list:
    """The evidence block the code under one import root prints."""
    result = subprocess.run([sys.executable, "-I", "-c", CHILD, str(root), str(home)],
                            capture_output=True, text=True, cwd=str(cwd))
    if result.returncode:
        raise Undecidable("the projection failed under " + str(root) + ": "
                          + result.stderr.strip()[-300:])
    try:
        lines = json.loads(result.stdout)
    except ValueError:
        raise Undecidable("the projection under " + str(root) + " printed an unreadable block")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise Undecidable("the projection under " + str(root) + " printed no block")
    return lines


def renderer_root(home: Path, slug: str) -> str:
    """The import root the plant's own renderer script gives the projection.

    The renderer script is what the pane runs. When it cannot be read the
    attribution is unknown, which is reported as a note and not as a finding:
    the question this check asks is answered by the published frame itself.
    """
    path = home / "top-pains" / slug
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "unknown (no readable renderer script at " + str(path) + ")"
    for match in PYTHONPATH_RE.finditer(text):
        candidate = Path(match.group(1))
        if (candidate / PACKAGE / "__init__.py").is_file():
            return str(candidate)
    return "unknown (the renderer script names no plant import root)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--slug", required=True, help="role whose published evidence is compared")
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
    workdir = Path(tempfile.mkdtemp(prefix="observer-view-agreement-"))
    try:
        reference = (args.reference_src.resolve() if args.reference_src
                     else export_head(repo, workdir))
        published, created = published_section(home, args.slug)
        expected = read_lines(reference, home, workdir)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    shown = projections(published)
    wanted = projections(expected)
    missing = [key for key in wanted if key not in shown]
    extra = [key for key in shown if key not in wanted]

    print("published  :", home / "dashboards" / (args.slug + ".json"), "at", created)
    print("reference  :", reference)
    print("renderer   :", renderer_root(home, args.slug))
    print("projections: published=%d reference=%d" % (len(shown), len(wanted)))
    if missing:
        print("published  : MISSING — the plant does not print %d projection(s) "
              "the reviewed tree publishes" % len(missing))
        for key in missing:
            print("    missing :", key)
            print("        reviewed line:", wanted[key][:400])
    if extra:
        print("published  : EXTRA — the plant prints %d projection(s) the reviewed "
              "tree does not publish" % len(extra))
        for key in extra:
            print("    extra   :", key)
    if not missing and not extra:
        print("published  : AGREE — the plant's published evidence carries every "
              "projection the reviewed tree publishes")
    if missing or extra:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
