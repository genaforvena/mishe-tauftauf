#!/usr/bin/env python3
"""Detector: a live unit that runs plant code must be observed by the plant.

The plant knows which bytes it runs: the service manifest
(``health/services.json``) lists the units whose health and runtime verdicts it
reads, and the provenance record (``health/declared-roots.json``) declares the
off-pin roots it knows about on purpose. A unit that runs plant code and is in
neither list is a unit the plant does not observe at all: nothing the plant
publishes says whether its bytes are current, and a fix committed to the
reviewed tree stays inert while that unit keeps running the old behaviour.

This is not hypothetical. On 2026-10-21 the initiative escalation monitor
(``mishe-self-development-current-initiative-watch.service``) ran a snapshot
whose bytes predate the reviewed fix ``b58b03d`` (owner-only reminders with a
backing-off schedule). The plant published ``RUNTIME: DECLARED`` and
``GREEN — listed services running`` while the live monitor fanned one
unchanged condition to four roles every thirty minutes for seven hours, about
1,300 notices a day, and the role that owned the condition was the least
notified of all.

The check answers one question, read-only:

  1. Which live user units run plant code from an **off-pin import root** — a
     root that is neither the pinned release nor the reviewed checkout?
  2. Is each such unit observed by the plant — listed in the service manifest,
     or declared in the provenance record?
  3. Do the bytes that root actually carries match the reviewed tree's HEAD?

A unit on the pinned release is *observed by construction*: the plant reads
the pin and the release is a reviewed commit, so a difference from HEAD there
is a pin advance the plant already knows about, not a blind spot. A unit on a
hand-assembled root is different: nothing says which reviewed commit produced
those bytes, and a fix committed to the shared checkout stays inert while the
unit keeps running the old behaviour. Those are the units this check names.

A unit in neither list is **unobserved**: the plant publishes no verdict about
its bytes, so the question is not "the bytes are stale" but "nobody is
checking". That is reported as a finding, not as a pass.

Exit status:
  0  every live unit that runs plant code from an off-pin root is observed, and
     every such root's package bytes are the reviewed HEAD's bytes
  2  an off-pin unit is unobserved, or an observed off-pin root carries bytes
     that are not HEAD's
  3  the question cannot be decided: the live units cannot be read, the
     manifest or the provenance record is missing or malformed, or no
     repository holds the reviewed tree
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "mishe_tauftauf"
PLANT_PACKAGES = (PACKAGE, "coordination")
REPO_PACKAGE = "src/" + PACKAGE
SERVICES = "health/services.json"
DECLARED = "health/declared-roots.json"
PLANT_CODE_MARKER = "mishe_tauftauf"


class Undecidable(Exception):
    """The checker lacks the evidence to decide, and says so."""


def _run(args, check=True, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise Undecidable(result.stderr.strip() or "command failed: " + " ".join(args))
    return result


def _json(path):
    """Read one JSON document, refusing a duplicate key or a non-finite number."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-finite number: " + value)

    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          object_pairs_hook=pairs,
                          parse_constant=constant)
    except (OSError, ValueError, UnicodeError) as exc:
        raise Undecidable("cannot read " + str(path) + ": " + type(exc).__name__)


def live_units():
    """The live user units the manager reports, by name."""
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid()))
    result = subprocess.run(
        ["systemctl", "--user", "list-units", "--type=service", "--all",
         "--no-legend", "--plain", "--no-pager"],
        capture_output=True, text=True, timeout=30, env=env)
    if result.returncode:
        raise Undecidable("the live user units cannot be read")
    names = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0].endswith(".service"):
            names.append(fields[0])
    if not names:
        raise Undecidable("the live user unit list is empty")
    return sorted(set(names))


def unit_environment(unit):
    """A unit's own environment, or an empty string when it has none."""
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid()))
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "Environment", "--value"],
        capture_output=True, text=True, timeout=20, env=env)
    if result.returncode:
        return ""
    return " ".join(result.stdout.split())


def unit_roots(unit):
    """The PYTHONPATH import roots a live user unit runs from."""
    roots = []
    for field in unit_environment(unit).split():
        key, _, value = field.partition("=")
        if key == "PYTHONPATH":
            roots.extend(part for part in value.split(":") if part)
    return roots


def unit_runs_plant_code(unit):
    """Whether a live unit runs the plant's own code."""
    environment = unit_environment(unit)
    if PLANT_CODE_MARKER in environment:
        return True
    result = _run(["systemctl", "--user", "show", unit, "-p", "ExecStart", "--value"],
                  check=False)
    return PLANT_CODE_MARKER in result.stdout


def manifest_units(home):
    """The units the plant's service manifest lists."""
    value = _json(home / SERVICES)
    if not isinstance(value, list) or any(
            not isinstance(unit, str) or not unit.endswith(".service")
            or unit.startswith("-") or "/" in unit or any(c.isspace() for c in unit)
            for unit in value):
        raise Undecidable("the service manifest is malformed")
    return set(value)


def declared_units(home):
    """The units the plant's provenance record declares off-pin."""
    value = _json(home / DECLARED)
    if not isinstance(value, dict) or not isinstance(value.get("units"), dict):
        raise Undecidable("the provenance record is malformed")
    units = {}
    for unit, entry in value["units"].items():
        if not isinstance(unit, str) or not unit.endswith(".service") or not isinstance(entry, dict):
            raise Undecidable("the provenance record is malformed")
        units[unit] = entry
    return units


def head_commit(repo):
    result = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=False)
    if result.returncode:
        raise Undecidable("no reviewed repository at " + str(repo))
    return result.stdout.strip()


def commit_blobs(repo, commit, relative):
    """The blob ids one commit holds under a path."""
    listed = _run(["git", "-C", str(repo), "ls-tree", commit, relative + "/"],
                  check=False)
    if listed.returncode:
        return {}
    blobs = {}
    prefix = relative.rstrip("/") + "/"
    for line in listed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[1] != "blob":
            continue
        path = fields[3]
        if not path.startswith(prefix):
            continue
        name = path[len(prefix):]
        # Only the modules directly under the directory count; a subpackage
        # is a different tree and is compared by its own path.
        if "/" in name or not name.endswith(".py"):
            continue
        blobs[name] = fields[2]
    return blobs


def module_blobs(repo, directory):
    """The git blob ids of the Python modules in one package directory."""
    blobs = {}
    if not directory.is_dir():
        return blobs
    for path in sorted(directory.glob("*.py")):
        result = _run(["git", "-C", str(repo), "hash-object", str(path)], check=False)
        if result.returncode:
            continue
        blobs[path.name] = result.stdout.strip()
    return blobs


def package_dirs(root):
    """The plant package directories one import root carries."""
    found = []
    for package in PLANT_PACKAGES:
        directory = Path(root) / package
        if (directory / "__init__.py").is_file():
            found.append(directory)
    return found


def differing_modules(repo, directory, head, prefix=REPO_PACKAGE):
    """The modules one copy carries that HEAD's tree does not carry."""
    head_tree = commit_blobs(repo, head, prefix)
    blobs = module_blobs(repo, directory)
    changed = sorted(name for name, blob in blobs.items() if head_tree.get(name) != blob)
    missing = sorted(name for name in head_tree if name not in blobs)
    extra = sorted(name for name in blobs if name not in head_tree)
    return changed, missing, extra


def root_is_current(repo, directory, head):
    """Whether one package directory carries HEAD's bytes, and what differs."""
    changed, missing, extra = differing_modules(repo, directory, head)
    if not changed and not missing and not extra:
        return True, []
    return False, changed + missing + extra


def root_carries_commit(repo, directory, commit):
    """Whether one package directory carries a named commit's bytes.

    A root the plant declares is declared *with* a commit: the record says
    which reviewed commit was copied there, and the honest question is whether
    those bytes are that commit's. Comparing it with HEAD instead would call
    a deliberately pinned, correctly declared root stale.
    """
    blobs = module_blobs(repo, directory)
    if not blobs:
        return False, []
    commit_tree = commit_blobs(repo, commit, REPO_PACKAGE)
    changed = sorted(name for name, blob in blobs.items() if commit_tree.get(name) != blob)
    missing = sorted(name for name in commit_tree if name not in blobs)
    extra = sorted(name for name in blobs if name not in commit_tree)
    if not changed and not missing and not extra:
        return True, []
    return False, changed + missing + extra


def pinned_root(home):
    """The import roots the plant's own pin names, or an empty list.

    The pin records the release directory; a unit names the release's ``src``
    directory. Both are the pin's bytes, so both are recognised.
    """
    path = home / "health" / "runtime-release.json"
    try:
        value = _json(path)
    except Undecidable:
        return []
    if not isinstance(value, dict):
        return []
    roots = []
    for key in ("root", "source", "release"):
        candidate = value.get(key)
        if not isinstance(candidate, str) or not candidate:
            continue
        base = Path(candidate).resolve()
        roots.append(str(base))
        roots.append(str(base / "src"))
    return roots


def off_pin_roots(roots, pin, checkout):
    """The roots that are neither the pinned release nor the reviewed checkout."""
    off = []
    for root in roots:
        try:
            resolved = str(Path(root).resolve())
        except OSError:
            off.append(root)
            continue
        if resolved in pin:
            continue
        if checkout is not None and resolved == str(checkout):
            continue
        off.append(resolved)
    return off


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--unit", action="append", default=[],
                        help="check only this unit (repeatable)")
    parser.add_argument("--session-prefix", default="mishe-self-development-current-",
                        help="the unit prefix of this site (default: the site's own session)")
    args = parser.parse_args(argv)
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    try:
        if not (repo / ".git").exists():
            raise Undecidable("no repository at " + str(repo))
        head = head_commit(repo)
        manifest = manifest_units(home)
        declared = declared_units(home)
        pin = pinned_root(home)
        checkout = str(repo / "src")
        units = args.unit or live_units()
        findings = []
        for unit in units:
            # Another site's units are that site's business; this check answers
            # for the site whose home it was given.
            if not args.unit and args.session_prefix and not unit.startswith(args.session_prefix):
                continue
            if not unit_runs_plant_code(unit):
                continue
            roots = off_pin_roots(unit_roots(unit), pin, checkout)
            if not roots:
                continue
            observed = unit in manifest or unit in declared
            if not observed:
                findings.append({
                    "unit": unit,
                    "state": "unobserved",
                    "roots": roots,
                    "detail": "runs plant code from an off-pin root and is in neither "
                              + SERVICES + " nor " + DECLARED
                              + "; the plant publishes no verdict about its bytes",
                })
                continue
            differing = []
            for root in roots:
                for directory in package_dirs(root):
                    # A declared root is declared *with* a commit, so its
                    # honest test is that commit's bytes, not HEAD's.
                    commit = (declared.get(unit) or {}).get("reviewed_commit")
                    if commit and unit in declared:
                        current, names = root_carries_commit(repo, directory, commit)
                    else:
                        current, names = root_is_current(repo, directory, head)
                    if not current:
                        differing.extend(names)
            if differing:
                findings.append({
                    "unit": unit,
                    "state": "stale",
                    "roots": roots,
                    "detail": "observed, but its bytes are not the reviewed ones: "
                              + ", ".join(sorted(set(differing))[:8])
                              + " (" + str(len(set(differing))) + " module(s))",
                })
            else:
                findings.append({
                    "unit": unit,
                    "state": "current",
                    "roots": roots,
                    "detail": "observed and carries the reviewed bytes it declares",
                })
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        print("UNKNOWN — " + type(exc).__name__)
        return 3
    if not findings:
        print("no live unit of this site runs plant code from an off-pin root")
        return 0
    for finding in findings:
        print("%s %s roots=%s %s" % (finding["state"], finding["unit"],
                                     ",".join(finding["roots"]) or "-",
                                     finding["detail"]))
    if all(finding["state"] == "current" for finding in findings):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
