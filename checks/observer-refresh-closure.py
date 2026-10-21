#!/usr/bin/env python3
"""Detector: the observer bundle's refresh is a closure, not a file.

The plant's shared follow-through evidence is produced by the observer bundle
(``observer-initiative-20261020``), a hand copy of the reviewed tree that is
refreshed one module at a time, when a reading is observed to be wrong. The
reviewed tree's ``follow_through.py`` imports ``OUTCOME_REFUSAL_AUDIT`` from
``wall.py``, and the reviewed tree's ``wall.py`` imports ``blocking_gaps`` from
``outcome_events.py``. The bundle's copies of those two modules predate those
names.

So refreshing the one file that carries the new projection breaks the evidence
leg: the bundle's ``follow_through`` cannot import, and the block it publishes
collapses to a bare header.

The failure is invisible to the plant. Every top-pain script runs the evidence
child *last* and exits with the **first** command's status, so a failed
evidence child leaves the renderer exiting 0 with a truncated block, and the
dashboard publishes that frame as ``ok``.

This check measures the closure the running bundle actually needs, by
discovering it from the import errors themselves rather than by reading the
source, and reports whether a one-file refresh would break the evidence leg.
It reads the plant; every copy it makes is disposable.

Exit status:
  0  the running bundle's evidence leg works, and a one-file refresh of the
     reviewed projection would not break it
  2  a one-file refresh of the reviewed projection would break the evidence
     leg: the closure is larger than one file
  3  the question cannot be decided: no bundle, no reviewed tree, an
     unreadable module, or a closure that does not converge
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
home = Path(sys.argv[2])
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

print(json.dumps(follow_through.lines(home)))
"""

# The two shapes an import failure takes inside the package: a name the
# reviewed module expects from a sibling, and a sibling that is missing.
MISSING_NAME = re.compile(r"from 'mishe_tauftauf\.(\w+)'")
MISSING_MODULE = re.compile(r"No module named 'mishe_tauftauf\.(\w+)'")


class Undecidable(Exception):
    """The check lacks the evidence to decide, and says so."""


def bundle_root(home: Path) -> Path:
    """The import root the plant's evidence child runs from.

    The root is named by the site's own renderer script, so the check measures
    the bytes the plant runs rather than a copy it prefers.
    """
    for name in ("top-pains/research-methods", "top-pains/genome", "top-pains/health"):
        path = home / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in re.finditer(r"PYTHONPATH=['\"]?([^'\"\s:]+)", text):
            candidate = Path(match.group(1))
            if (candidate / PACKAGE / "__init__.py").is_file():
                return candidate
    raise Undecidable("no readable renderer script names a plant import root")


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


def run_child(root: Path, home: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Ask the code under one import root for the evidence block it prints."""
    return subprocess.run([sys.executable, "-I", "-c", CHILD, str(root), str(home)],
                          capture_output=True, text=True, cwd=str(cwd))


def evidence_lines(root: Path, home: Path, cwd: Path) -> list:
    """The block the evidence leg prints, or the failure that stopped it."""
    result = run_child(root, home, cwd)
    if result.returncode:
        raise Undecidable("the evidence leg failed under " + str(root) + ": "
                          + result.stderr.strip()[-300:])
    try:
        lines = json.loads(result.stdout)
    except ValueError:
        raise Undecidable("the evidence leg under " + str(root)
                          + " printed an unreadable block")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise Undecidable("the evidence leg under " + str(root) + " printed no block")
    return lines


def missing_sibling(stderr: str) -> str:
    """The sibling module an import failure names, if it names one."""
    for pattern in (MISSING_NAME, MISSING_MODULE):
        match = pattern.search(stderr)
        if match:
            return match.group(1)
    return ""


def closure(root: Path, reference: Path, workdir: Path, home: Path, cwd: Path) -> list:
    """The files that must be refreshed together for the evidence leg to work.

    The closure is discovered from the import errors the refresh itself
    produces: refresh the reviewed projection, read the failure, refresh the
    sibling it names, repeat. Reading the source instead would guess at what
    a future refresh needs; running it measures it.
    """
    copy = workdir / "closure"
    shutil.copytree(root, copy)
    refreshed = {"follow_through.py"}
    for _ in range(12):
        for name in refreshed:
            shutil.copy(reference / PACKAGE / name, copy / PACKAGE / name)
        result = run_child(copy, home, cwd)
        if result.returncode == 0:
            return sorted(refreshed)
        sibling = missing_sibling(result.stderr)
        if not sibling:
            raise Undecidable("the refresh failed in a way no sibling names: "
                              + result.stderr.strip()[-300:])
        candidate = sibling + ".py"
        if candidate in refreshed or not (reference / PACKAGE / candidate).is_file():
            raise Undecidable("the refresh needs " + sibling + ", which the "
                              "reviewed tree does not carry")
        refreshed.add(candidate)
    raise Undecidable("the refresh closure did not converge")


def one_file_refresh(root: Path, reference: Path, workdir: Path,
                     home: Path, cwd: Path) -> tuple:
    """What refreshing only the projection does to the evidence leg.

    The projection is the one file a reader would refresh: it is the file that
    carries the new observation. This measures what that single refresh costs.
    """
    copy = workdir / "one-file"
    shutil.copytree(root, copy)
    shutil.copy(reference / PACKAGE / "follow_through.py", copy / PACKAGE / "follow_through.py")
    result = run_child(copy, home, cwd)
    if result.returncode == 0:
        return True, ""
    return False, result.stderr.strip().splitlines()[-1][:200]


def renderer_swallows_failure(home: Path, slug: str, workdir: Path,
                               root: Path, broken_root: Path) -> tuple:
    """Whether a failed evidence child is visible in the renderer's exit status.

    Every top-pain script runs the evidence child *after* it captures the
    previous command's status, and exits with that captured status. A failed
    evidence child therefore leaves the renderer exiting 0.

    The renderer script is copied and every mention of the bundle root is
    redirected at a disposable broken copy, so the plant's own script is
    measured and the plant's own bundle is not touched.
    """
    script = home / "top-pains" / slug
    try:
        text = script.read_text(encoding="utf-8")
    except OSError as exc:
        raise Undecidable("no readable renderer script at " + str(script)
                          + " (" + type(exc).__name__ + ")")
    redirected = text.replace(str(root), str(broken_root))
    copy = workdir / "renderer"
    copy.write_text(redirected, encoding="utf-8")
    copy.chmod(0o755)
    result = subprocess.run([str(copy)], capture_output=True, text=True,
                            cwd=str(workdir), timeout=60)
    block = result.stdout
    marker = "MIND WALL — shared follow-through evidence"
    index = block.find(marker)
    published = len(block) - index if index >= 0 else 0
    return result.returncode == 0, published


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--reference-src", type=Path, default=None,
                        help="import root of the reference tree (default: a clean HEAD export)")
    parser.add_argument("--slug", default="research-methods",
                        help="role whose renderer script is measured for silence")
    args = parser.parse_args()
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    workdir = Path(tempfile.mkdtemp(prefix="observer-refresh-closure-"))
    try:
        try:
            root = bundle_root(home)
            reference = (args.reference_src.resolve() if args.reference_src
                         else export_head(repo, workdir))
            if not (reference / PACKAGE / "follow_through.py").is_file():
                raise Undecidable("the reference tree at " + str(reference)
                                  + " carries no plant package")
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        print("bundle    :", root)
        print("reference :", reference)
        try:
            live = evidence_lines(root, home, repo)
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        print("evidence leg under the running bundle: %d line(s) published" % len(live))
        try:
            wanted = closure(root, reference, workdir, home, repo)
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        print("closure   :", ", ".join(wanted))
        print("            (the reviewed projection and the siblings it needs)")
        safe, reason = one_file_refresh(root, reference, workdir, home, repo)
        if safe:
            print("verdict   : SAFE — refreshing only follow_through.py leaves the "
                  "evidence leg working")
            return 0
        print("verdict   : UNSAFE — refreshing only follow_through.py breaks the "
              "evidence leg")
        print("            ", reason)
        print("cause     : the bundle's siblings predate the names the reviewed "
              "projection imports,")
        print("            so the refresh must carry the closure above, not one file")
        try:
            swallowed, published = renderer_swallows_failure(
                home, args.slug, workdir, root, workdir / "one-file")
        except Undecidable as exc:
            print("visibility : UNKNOWN — " + str(exc))
            return 2
        if swallowed:
            print("visibility : SILENT — the renderer still exits 0 with the "
                  "evidence child failed,")
            print("             and the block it publishes carries %d byte(s) of "
                  "evidence" % published)
            print("             so the plant would publish a truncated frame and "
                  "report it as ok")
        else:
            print("visibility : VISIBLE — the renderer exits non-zero, so a failed "
                  "evidence child is reported")
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
