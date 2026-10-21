#!/usr/bin/env python3
"""Detector: a live observer service's import root must be a reviewed tree.

A plant service that runs an import root assembled by hand is a plant whose
runtime bytes have no commit identity. Nothing in the plant can then say which
source produced the running behaviour, no release or pin advance reaches it,
and a fix committed to the shared checkout stays unobserved while the service
keeps reporting the old verdict. The substrate observer is exactly that case:
its import root is a directory of copied modules whose files come from several
different commits, so the bytes it runs match no tree the plant ever reviewed.

This checker answers one question, read-only:

  1. Which import roots does a named live unit actually run from?
  2. For each root, is the plant package it carries the reviewed working tree,
     a reviewed commit, or a mixture of several commits (a hand copy)?
  3. Does that identity match the current HEAD of the reviewed repository?

With ``--declared`` it checks the site's own provenance record instead: every
root the site declares off-pin names the commit it says was reviewed, and the
check says whether the bytes that root actually carries are that commit's
bytes. A declared root is a decision the plant records and acts on, so a
record that overstates what was reviewed is a defect the plant can feel.

Exit status:
  0  every root's package bytes are the reviewed HEAD bytes (or, under
     --declared, every declared root carries its declared commit's bytes)
  2  a root carries bytes that are not HEAD's: mixed across commits, an older
     commit, an uncommitted working tree, or bytes that differ from the commit
     the site's own record declares for that root
  3  the question cannot be decided: no unit, no import root, no repository,
     an unreadable module file, or an unreadable provenance record
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PACKAGE = "mishe_tauftauf"
PLANT_PACKAGES = (PACKAGE, "coordination")
REPO_PACKAGE = "src/" + PACKAGE
DECLARED_MANIFEST = "health/declared-roots.json"


class Undecidable(Exception):
    """The checker lacks the evidence to decide, and says so."""


def _run(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise Undecidable("command failed: " + " ".join(args[:3]))
    return result


def unit_import_roots(unit, session_prefix="mishe-self-development-current-"):
    """The PYTHONPATH import roots a live user unit runs from.

    The unit's own environment is the observation; a unit that is not loaded
    or names no import root leaves the question undecided rather than green.
    """
    name = unit if unit.endswith(".service") else session_prefix + unit + ".service"
    result = _run(["systemctl", "--user", "show", name, "-p", "Environment", "--value"],
                  check=False)
    if result.returncode:
        raise Undecidable("unit not observable: " + name)
    roots = []
    for field in result.stdout.split():
        key, _, value = field.partition("=")
        if key == "PYTHONPATH":
            roots.extend(part for part in value.split(":") if part)
    if not roots:
        raise Undecidable("unit names no import root: " + name)
    return name, roots


def package_dirs(root):
    """The plant package directories one import root carries.

    Only the plant's own packages are judged: a root may also expose a test
    package or a third-party directory, and those are not the bytes a live
    service runs the plant from.
    """
    root = Path(root)
    found = []
    for name in PLANT_PACKAGES:
        directory = root / name
        if directory.is_dir():
            found.append(directory)
    if not found and any(root.glob("*.py")):
        found.append(root)
    return found


def module_blobs(repo, directory):
    """Git blob ids of the Python modules in one package directory."""
    modules = sorted(p for p in Path(directory).glob("*.py") if p.is_file())
    if not modules:
        raise Undecidable("package holds no module: " + str(directory))
    blobs = {}
    for module in modules:
        result = _run(["git", "-C", str(repo), "hash-object", str(module.resolve())],
                      check=False)
        if result.returncode:
            raise Undecidable("module not readable: " + str(module))
        blobs[module.name] = result.stdout.strip()
    return blobs


def _package_commits(repo, prefix=REPO_PACKAGE):
    """Every commit that changed the plant package directory."""
    result = _run(["git", "-C", str(repo), "log", "--format=%H", "--all", "--", prefix],
                  check=False)
    return result.stdout.split()


def _tree_blobs(repo, commit, prefix=REPO_PACKAGE):
    """The top-level Python module blobs one commit holds under a package."""
    listed = _run(["git", "-C", str(repo), "ls-tree", commit + ":" + prefix], check=False)
    if listed.returncode:
        return {}
    blobs = {}
    for line in listed.stdout.splitlines():
        meta, _, name = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob" and name.endswith(".py"):
            blobs[name.rsplit("/", 1)[-1]] = parts[2]
    return blobs


def reproducing_commits(repo, blobs, prefix=REPO_PACKAGE):
    """Commits whose whole package tree exactly reproduces these module bytes.

    Both blob contents and the package module set must match. A copy that omits
    a module introduced later still carries the older tree; subset matching
    would incorrectly make multiple historical commits reproduce it.
    """
    candidates = _package_commits(repo, prefix)
    if not candidates:
        return set()
    reproducing = set()
    for commit in candidates:
        tree = _tree_blobs(repo, commit, prefix)
        if tree == blobs:
            reproducing.add(commit)
    return reproducing


def head_commit(repo):
    result = _run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=False)
    if result.returncode:
        raise Undecidable("repository has no HEAD")
    return result.stdout.strip()


def declared_roots(home):
    """The off-pin roots the site declares, with the commit each one claims.

    An absent or malformed record declares nothing the check can judge, so it
    leaves the question undecided rather than green.
    """
    try:
        record = json.loads((Path(home) / DECLARED_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Undecidable("provenance record unreadable: " + str(exc))
    units = record.get("units") if isinstance(record, dict) else None
    if not isinstance(units, dict):
        raise Undecidable("provenance record names no units")
    declared = []
    for unit, entry in units.items():
        if not isinstance(entry, dict):
            continue
        root = entry.get("root")
        commit = entry.get("reviewed_commit")
        if isinstance(unit, str) and isinstance(root, str) and root:
            declared.append((unit, root, commit if isinstance(commit, str) else None))
    if not declared:
        raise Undecidable("provenance record declares no root")
    return declared


def declared_finding(repo, directory, commit):
    """Whether a package directory carries the commit its record declares."""
    blobs = module_blobs(repo, directory)
    if not commit:
        return {"root": str(directory), "state": "undeclared",
                "detail": "no reviewed commit named for this root",
                "modules": len(blobs)}
    tree = _tree_blobs(repo, commit, REPO_PACKAGE)
    if not tree:
        return {"root": str(directory), "state": "unknown-commit",
                "detail": "declared commit " + commit[:12] + " is not in the repository",
                "modules": len(blobs)}
    if tree != blobs:
        changed = sorted(name for name, blob in blobs.items() if tree.get(name) != blob)
        missing = sorted(name for name in tree if name not in blobs)
        extra = sorted(name for name in blobs if name not in tree)
        return {"root": str(directory), "state": "differs",
                "detail": "declared commit difference: changed=%d [%s], missing=%d [%s], extra=%d [%s]"
                          % (len(changed), ", ".join(changed[:8]),
                             len(missing), ", ".join(missing[:8]),
                             len(extra), ", ".join(extra[:8])),
                "modules": len(blobs)}
    return {"root": str(directory), "state": "current", "commit": commit,
            "detail": "root carries the declared commit's bytes",
            "modules": len(blobs)}


def tracked_relative(repo, directory):
    """The repository path of a package directory, or None.

    A package directory inside the reviewed repository is a working tree, not a
    hand copy: its bytes are the tree plus whatever is uncommitted there. A
    package directory outside it is a copy, and only a commit can give its
    bytes an identity.
    """
    directory = Path(directory).resolve()
    repo = Path(repo).resolve()
    try:
        relative = directory.relative_to(repo)
    except ValueError:
        return None
    listed = _run(["git", "-C", str(repo), "ls-tree", "--name-only", "HEAD",
                   relative.as_posix() + "/"], check=False)
    if listed.returncode:
        return None
    modules = [name for name in listed.stdout.split()
               if name.endswith(".py") and (repo / name).is_file()]
    return relative.as_posix() if modules else None


def head_blobs(repo, relative):
    """The HEAD blob ids of the Python modules directly under a tracked path."""
    return commit_blobs(repo, "HEAD", relative)


def commit_blobs(repo, commit, relative):
    """The blob ids of the Python modules one commit holds under a path."""
    listed = _run(["git", "-C", str(repo), "ls-tree", commit, relative + "/"], check=False)
    if listed.returncode:
        return {}
    blobs = {}
    for line in listed.stdout.splitlines():
        meta, _, name = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob" and name.endswith(".py"):
            blobs[name.rsplit("/", 1)[-1]] = parts[2]
    return blobs


def classify_tracked(repo, directory, relative, blobs):
    """A package directory that is the repository's own working tree."""
    head = head_blobs(repo, relative)
    if not head:
        raise Undecidable("no tracked module under " + relative)
    changed = sorted(name for name, blob in blobs.items() if head.get(name) != blob)
    missing = sorted(name for name in head if name not in blobs)
    extra = sorted(name for name in blobs if name not in head)
    if changed or missing:
        return {"root": str(directory), "state": "dirty",
                "detail": "uncommitted module bytes: changed=%d missing=%d extra=%d"
                          % (len(changed), len(missing), len(extra)),
                "modules": len(blobs)}
    return {"root": str(directory), "state": "current", "commit": "HEAD",
            "detail": "working tree matches HEAD", "modules": len(blobs)}


def differing_modules(repo, directory, head, blobs=None, prefix=REPO_PACKAGE):
    """The modules one copy carries that HEAD's tree does not carry.

    The question is byte identity, so it is answered by comparing the copy's
    module blobs with the blobs HEAD's tree holds under the same path. A
    commit that did not change the package directory is not a candidate for
    "the commit that reproduces these bytes", so a copy whose bytes are
    HEAD's own would otherwise be named after an older commit that last
    touched the directory.
    """
    head_tree = commit_blobs(repo, head, prefix)
    if blobs is None:
        blobs = module_blobs(repo, directory)
    changed = sorted(name for name, blob in blobs.items() if head_tree.get(name) != blob)
    missing = sorted(name for name in head_tree if name not in blobs)
    extra = sorted(name for name in blobs if name not in head_tree)
    return changed, missing, extra


def classify_package(repo, directory, head):
    """One package directory: which reviewed commit, if any, reproduces it."""
    blobs = module_blobs(repo, directory)
    tracked = tracked_relative(repo, directory)
    if tracked:
        return classify_tracked(repo, directory, tracked, blobs)
    changed, missing, extra = differing_modules(repo, directory, head, blobs)
    if not changed and not missing and not extra:
        return {"root": str(directory), "state": "current", "commit": head,
                "detail": "running tree carries HEAD's bytes", "modules": len(blobs)}
    commits = reproducing_commits(repo, blobs)
    if not commits:
        return {"root": str(directory), "state": "mixed",
                "detail": "modules come from more than one commit; no reviewed tree reproduces them",
                "modules": len(blobs)}
    if len(commits) > 1:
        return {"root": str(directory), "state": "ambiguous",
                "detail": "modules reproduce " + str(len(commits)) + " commits",
                "modules": len(blobs)}
    commit = sorted(commits)[0]
    if commit != head:
        return {"root": str(directory), "state": "stale", "commit": commit,
                "detail": "running tree reproduces " + commit[:12] + ", HEAD is " + head[:12]
                          + "; HEAD difference: changed=%d [%s], missing=%d [%s], extra=%d [%s]"
                          % (len(changed), ", ".join(changed[:8]),
                             len(missing), ", ".join(missing[:8]),
                             len(extra), ", ".join(extra[:8])),
                "modules": len(blobs)}
    return {"root": str(directory), "state": "current", "commit": commit,
            "detail": "running tree reproduces HEAD", "modules": len(blobs)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--unit", default="substrate-watch",
                        help="unit whose import root is checked (default: substrate-watch)")
    parser.add_argument("--import-root", action="append", default=[],
                        help="check this import root instead of reading a unit")
    parser.add_argument("--declared", action="store_true",
                        help="check the site's declared off-pin roots against the commit each declares")
    args = parser.parse_args()
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    try:
        if not (repo / ".git").exists():
            raise Undecidable("no repository at " + str(repo))
        head = head_commit(repo)
        findings = []
        seen = set()
        if args.declared:
            for unit, root, commit in declared_roots(home):
                for directory in package_dirs(Path(root)):
                    key = str(directory)
                    if key in seen:
                        continue
                    seen.add(key)
                    finding = declared_finding(repo, directory, commit)
                    finding["unit"] = unit
                    findings.append(finding)
        elif args.import_root:
            roots = [Path(p).resolve() for p in args.import_root]
            for root in roots:
                for directory in package_dirs(root):
                    key = str(directory)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(classify_package(repo, directory, head))
        else:
            _, roots = unit_import_roots(args.unit)
            for root in roots:
                for directory in package_dirs(root):
                    key = str(directory)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(classify_package(repo, directory, head))
        if not findings:
            raise Undecidable("no import root carries a plant package")
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    for finding in findings:
        unit = finding.get("unit")
        label = ("%s %s" % (unit, finding["root"])) if unit else finding["root"]
        print("%s %s modules=%d %s" % (finding["state"], label,
                                        finding["modules"], finding["detail"]))
    if all(finding["state"] == "current" for finding in findings):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
