#!/usr/bin/env python3
"""Detector: the plant's published evidence must carry the reviewed fields.

``checks/observer-view-agreement.py`` compares the plant's published evidence
with the reviewed tree's at the level of *projections*: which observations are
present. On 2026-10-21 it reported the plant as MISSING ``OUTCOME WRITER
REFUSALS`` — the bundle's ``follow_through.py`` predates that projection, so
the line was absent entirely.

That comparison cannot see a line that is present but *incomplete*. The
observer bundle is refreshed one module at a time, so a bundle can carry a
projection whose line is missing a field the reviewed tree publishes, and the
projection is present, so the plant's check reads agreement. An absent
observation reads as a clean one; an observation with a field missing reads
the same way.

Measured on 2026-10-21: refreshing the three files that make the evidence leg
*run* — ``follow_through.py``, ``outcome_events.py``, ``wall.py``, the closure
``checks/observer-refresh-closure.py`` discovers — leaves the bundle's
``initiative.py`` stale. The leg then prints 35 lines and every role row lacks
the ``checked-evidence-age`` field the reviewed tree prints, while the
reviewed tree's rows carry it. The plant's projection-level check reports
AGREE on that state: the labels are the same, only a field is missing.

So this check compares at the level of *fields*: within a line the plant
publishes, does it carry every field the reviewed tree's line carries? Field
*names* are compared, never values: a counter that moves between two samples
is not a disagreement, and a missing field is not a value that changed.

Exit status:
  0  every published line carries the fields the reviewed tree's line carries
  2  a published line lacks a field the reviewed tree publishes, carries a
     field the reviewed tree does not, or the reviewed tree publishes a line
     the plant does not
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

# A field is a name the line binds or opens a group with: ``wakes=42`` and
# ``settled[changed=1]`` both name fields. Values are not compared: they move
# between samples, and a moving value is not a disagreement. Names carry
# hyphens (``checked-evidence-age=none``), so the pattern admits them.
FIELD_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*=")
FIELD_GROUP = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*\[")

# The child rebuilds its own import path: the given root plus the standard
# library. An ambient editable install would otherwise answer for the root
# under test, and the check would read agreement where there is none.
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
    """The class a line belongs to, without its value.

    The same rule the plant's projection-level check uses: the name before the
    first colon or equals sign. An indented line continues the class above it.
    """
    if not line or line[0].isspace():
        return ""
    text = line.strip()
    colon = text.find(":")
    equals = text.find("=")
    if colon != -1 and (equals == -1 or colon < equals):
        key = text[:colon].strip()
    elif equals != -1:
        key = text[:equals].strip()
    else:
        key = text.split(None, 1)[0].strip()
    if key.startswith("PATCH "):
        key = "PATCH"
    return key


def fields(line: str) -> set:
    """The field names a line binds or opens a group with."""
    names = set(FIELD_ASSIGN.findall(line))
    names.update(FIELD_GROUP.findall(line))
    return names


def groups(lines) -> dict:
    """The published lines of each class, and the fields that class carries.

    A class is a line plus the indented lines that continue it, so a class
    that spans several lines is compared as one class.
    """
    out = {}
    current = None
    for line in lines:
        if not isinstance(line, str) or not line.strip():
            continue
        key = label(line)
        if not key:
            if current is not None:
                out[current][1].add(line)
                out[current][2].update(fields(line))
            continue
        current = key
        out.setdefault(key, ([], set(), set()))
        out[key][0].append(line)
        out[key][2].update(fields(line))
    return out


def published_section(home: Path, slug: str) -> tuple:
    """The evidence block the plant published for one role, and the rest.

    The block is read from the published dashboard frame, because that frame
    is what every role's pane shows — the plant's own published output, not a
    re-run of it.
    """
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
    end = len(lines)
    for index in range(start, len(lines)):
        line = lines[index]
        if line.startswith("MIND WALL —"):
            end = index
            break
        if line.strip():
            body.append(line)
    if not body:
        raise Undecidable("the published evidence block is empty")
    return body, lines[:start - 1] + lines[end:], record.get("created")


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

    When it cannot be read the attribution is unknown, which is reported as a
    note and not as a finding.
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
    workdir = Path(tempfile.mkdtemp(prefix="observer-evidence-field-agreement-"))
    try:
        reference = (args.reference_src.resolve() if args.reference_src
                     else export_head(repo, workdir))
        published, _outside, created = published_section(home, args.slug)
        expected = read_lines(reference, home, workdir)
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    shown = groups(published)
    wanted = groups(expected)

    missing_lines = [key for key in wanted if key not in shown]
    missing_fields = []
    extra_fields = []
    for key, wanted_group in wanted.items():
        shown_group = shown.get(key)
        if shown_group is None:
            continue
        wanted_fields = wanted_group[2]
        shown_fields = shown_group[2]
        for name in sorted(wanted_fields - shown_fields):
            missing_fields.append((key, name))
        for name in sorted(shown_fields - wanted_fields):
            extra_fields.append((key, name))

    print("published  :", home / "dashboards" / (args.slug + ".json"), "at", created)
    print("reference  :", reference)
    print("renderer   :", renderer_root(home, args.slug))
    print("classes    : published=%d reference=%d" % (len(shown), len(wanted)))
    if missing_lines:
        print("published  : MISSING LINE — the plant does not publish %d line(s) "
              "the reviewed tree publishes" % len(missing_lines))
        for key in missing_lines:
            print("    missing :", key)
            print("        reviewed line:", wanted[key][0][0][:400])
    if missing_fields:
        print("published  : MISSING FIELD — %d published line(s) lack a field the "
              "reviewed tree publishes" % len(missing_fields))
        for key, name in missing_fields:
            print("    missing :", key, "->", name)
            print("        reviewed line:", wanted[key][0][0][:400])
            print("        published line:", shown[key][0][0][:400])
    if extra_fields:
        print("published  : EXTRA FIELD — %d published line(s) carry a field the "
              "reviewed tree does not publish" % len(extra_fields))
        for key, name in extra_fields:
            print("    extra   :", key, "->", name)
            print("        published line:", shown[key][0][0][:400])
    if not missing_lines and not missing_fields and not extra_fields:
        print("published  : AGREE — every published line carries the fields the "
              "reviewed tree's line carries")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
