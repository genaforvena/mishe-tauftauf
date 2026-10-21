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

The closure that the import errors reveal is a *run* closure: the files that
must be refreshed together for the evidence leg to work at all. It is not the
whole answer, because a module can import cleanly and still be stale. Such a
module is invisible to a discovery that reads import failures, and it
publishes the reviewed tree's older facts while every import succeeds.

So the check also finds the *fact* closure: the modules whose refresh brings
the published block into agreement with the reviewed tree's. Every module the
bundle's copy differs from is probed — the refresh is applied, the evidence
leg is run, and a module that brings the published facts closer to the
reviewed tree's is carried. A probe that cannot be carried alone is closed
the same way the run closure is, by running it and reading the failure.

Facts are compared with the counters masked: a counter that moves between two
samples of the same plant is not a difference of facts, while a field that is
present in one block and absent in the other is. The fact closure is the
smallest set this search found, not a proof of minimality.

Exit status:
  0  the running bundle publishes the reviewed tree's facts, and a one-file
     refresh of the reviewed projection would not break the evidence leg
  2  a one-file refresh of the reviewed projection would break the evidence
     leg: the closure is larger than one file
  3  the question cannot be decided: no bundle, no reviewed tree, an
     unreadable module, or a closure that does not converge
  4  the published facts differ from the reviewed tree's facts: the running
     bundle already differs, or a refresh carrying only the run closure would
     still differ. The report names the modules that carry them.
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
from collections import Counter
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

# A published line carries counters that move between two samples of the same
# plant: "wakes=32" in one run, "wakes=33" in the next. Masking the digits
# keeps a moving counter from reading as a difference of facts, while a field
# that is present in one block and absent in the other still reads as one.
DIGITS = re.compile(r"\d+")


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


def parse_block(stdout: str, root: Path) -> list:
    """The block the evidence leg printed, or the failure that stopped it."""
    try:
        lines = json.loads(stdout)
    except ValueError:
        raise Undecidable("the evidence leg under " + str(root)
                          + " printed an unreadable block")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise Undecidable("the evidence leg under " + str(root) + " printed no block")
    return lines


def evidence_lines(root: Path, home: Path, cwd: Path) -> list:
    """The block the code under one import root prints."""
    result = run_child(root, home, cwd)
    if result.returncode:
        raise Undecidable("the evidence leg failed under " + str(root) + ": "
                          + result.stderr.strip()[-300:])
    return parse_block(result.stdout, root)


def missing_sibling(stderr: str) -> str:
    """The sibling module an import failure names, if it names one."""
    for pattern in (MISSING_NAME, MISSING_MODULE):
        match = pattern.search(stderr)
        if match:
            return match.group(1)
    return ""


def mask(lines: list) -> list:
    """The published lines with their counters masked."""
    return [DIGITS.sub("N", line) for line in lines]


def fact_gap(published: list, reference: list) -> tuple:
    """The facts the reviewed tree publishes and the bundle does not, and back.

    A counter that moved between the two samples is not a difference of facts:
    the numbers are masked first. A field that is present in one block and
    absent in the other is a difference, and it is named on both sides.
    """
    left = Counter(mask(reference))
    right = Counter(mask(published))
    missing = sorted((left - right).elements())
    extra = sorted((right - left).elements())
    return missing, extra


def fact_distance(published: list, reference: list) -> int:
    """How many published facts differ between two blocks."""
    missing, extra = fact_gap(published, reference)
    return len(missing) + len(extra)


def apply_modules(root: Path, reference: Path, workdir: Path, name: str,
                  modules) -> Path:
    """A disposable copy of the bundle carrying the given reviewed modules."""
    copy = workdir / name
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(root, copy)
    for module in modules:
        source = reference / PACKAGE / module
        if source.is_file():
            shutil.copy(source, copy / PACKAGE / module)
    return copy


def probe(root: Path, reference: Path, workdir: Path, home: Path, cwd: Path,
          base: set, extra: str) -> tuple:
    """What refreshing one more module does to the published facts.

    The refresh is a closure, as the run closure is: if the module cannot be
    carried alone, the sibling it needs is added, by running the refresh and
    reading the failure. A probe never writes the plant's own bundle.
    """
    copy = workdir / ("probe-" + extra.replace(".", "-"))
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(root, copy)
    refreshed = set(base) | {extra}
    for _ in range(12):
        for module in refreshed:
            source = reference / PACKAGE / module
            if source.is_file():
                shutil.copy(source, copy / PACKAGE / module)
        result = run_child(copy, home, cwd)
        if result.returncode == 0:
            return refreshed, parse_block(result.stdout, copy)
        sibling = missing_sibling(result.stderr)
        if not sibling:
            raise Undecidable("the refresh of " + extra + " failed in a way no "
                              "sibling names: " + result.stderr.strip()[-300:])
        candidate = sibling + ".py"
        if candidate in refreshed or not (reference / PACKAGE / candidate).is_file():
            raise Undecidable("the refresh of " + extra + " needs " + sibling
                              + ", which the reviewed tree does not carry")
        refreshed.add(candidate)
    raise Undecidable("the refresh of " + extra + " did not converge")


def fact_closure(root: Path, reference: Path, workdir: Path, home: Path,
                 cwd: Path, base: list, reference_lines: list) -> dict:
    """The files a refresh must carry for the published facts to agree.

    The run closure is found from import failures; a module that imports
    cleanly and is still stale is invisible to that discovery. So every module
    the bundle's copy differs from is probed: the refresh is applied, the
    evidence leg is run, and a module that brings the published facts closer
    to the reviewed tree's is carried.

    The result is the smallest set this search found, not a proof of
    minimality: a module that only helps in combination with another may be
    left out, and the residual difference is reported when that happens.
    """
    candidates = []
    for path in sorted((root / PACKAGE).glob("*.py")):
        source = reference / PACKAGE / path.name
        if source.is_file() and source.read_bytes() != path.read_bytes():
            candidates.append(path.name)
    carried = set(base)
    copy = apply_modules(root, reference, workdir, "fact-closure", carried)
    lines = evidence_lines(copy, home, cwd)
    base_distance = fact_distance(lines, reference_lines)
    distance = base_distance
    for _ in range(2):
        if distance == 0:
            break
        progressed = False
        for name in candidates:
            if name in carried:
                continue
            try:
                probed, probed_lines = probe(root, reference, workdir, home,
                                             cwd, carried, name)
            except Undecidable:
                continue
            probed_distance = fact_distance(probed_lines, reference_lines)
            if probed_distance >= distance:
                continue
            carried = probed
            lines = probed_lines
            distance = probed_distance
            progressed = True
        if not progressed:
            break
    return {"carried": sorted(carried), "lines": lines,
            "added": sorted(set(carried) - set(base)),
            "base_distance": base_distance, "distance": distance,
            "candidates": candidates}


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
            reference_lines = evidence_lines(reference, home, repo)
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        print("evidence leg under the running bundle: %d line(s) published" % len(live))
        print("evidence leg under the reviewed tree: %d line(s) published" % len(reference_lines))
        missing, extra = fact_gap(live, reference_lines)
        facts_differ = bool(missing or extra)
        if facts_differ:
            print("facts     : DIFFER — the running bundle publishes %d fact(s) the "
                  "reviewed tree does not, and misses %d it publishes"
                  % (len(extra), len(missing)))
            for line in missing[:3]:
                print("            reviewed publishes:", line)
            for line in extra[:3]:
                print("            bundle publishes  :", line)
        else:
            print("facts     : AGREE — the running bundle publishes the reviewed "
                  "tree's facts")
        try:
            wanted = closure(root, reference, workdir, home, repo)
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        print("closure   :", ", ".join(wanted))
        print("            (the reviewed projection and the siblings it needs)")
        try:
            facts = fact_closure(root, reference, workdir, home, repo, wanted,
                                 reference_lines)
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        print("fact closure:", ", ".join(facts["carried"]))
        print("              (the run closure plus the modules that carry the facts)")
        if facts["added"]:
            print("              the run closure alone leaves %d fact(s) differing;"
                  % facts["base_distance"])
            print("              these modules carry them: " + ", ".join(facts["added"]))
        elif facts["base_distance"]:
            print("              the run closure alone leaves %d fact(s) differing, "
                  "and no single module closes them" % facts["base_distance"])
        if facts["distance"]:
            print("              residual: %d fact(s) still differ after the search"
                  % facts["distance"])
        safe, reason = one_file_refresh(root, reference, workdir, home, repo)
        if safe:
            print("verdict   : SAFE — refreshing only follow_through.py leaves the "
                  "evidence leg working")
        else:
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
        if facts_differ or facts["distance"]:
            return 4
        return 0 if safe else 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
