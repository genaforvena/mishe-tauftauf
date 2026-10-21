#!/usr/bin/env python3
"""Refresh the observer bundle's package from the reviewed tree, safely and reversibly.

The observer bundle is the import root the plant's own renderer scripts run
``mishe_tauftauf.follow_through`` from, so its bytes are what every role's
published evidence block is made of. On 2026-10-20 the bundle was a hand copy
of the reviewed tree taken at 16:40Z, and it was refreshed one module at a time
whenever a reading was observed to be wrong. That produced two failures, both
measured on the live plant:

* a one-file refresh of the reviewed projection broke the evidence leg,
  because the bundle's siblings predate the names the projection imports;
* a stale module that imports cleanly stayed stale, so the plant published
  role rows without a field the reviewed tree publishes, and every
  projection-level check read AGREE.

Neither failure is a discovery problem: both are the *order* of a refresh.
This tool makes the order a decision the tool makes, by running the refresh on
a disposable copy and reading the failure, exactly as
``checks/observer-refresh-closure.py`` does. The discovery is not
re-implemented here; that check is the plant's own and it is loaded by path.

What the tool does:

* **discovers** the set of files a refresh must carry -- the run closure
  (import errors) plus the fact closure (the modules that carry the published
  facts) -- on a disposable copy, never on the plant's own bundle;
* **verifies** the refresh before it touches the plant: the evidence leg must
  run under the refreshed copy and must publish the reviewed tree's facts;
* **writes** with a backup, a sha256 manifest and an activation record. The
  manifest is written *before* the first byte is copied, so a failure between
  the write and the record still leaves a route back to the old bytes;
* **rolls back** if the verification after the write fails, and says so
  honestly: a rollback that could not restore every byte is reported as
  ``ROLLBACK FAILED`` with the backup's path, not as a success;
* **reverts** from the manifest, restoring the exact previous bytes, and
  reports a module it could not restore.

Two modes:

``--mode facts`` (default)
    carry the minimal set that makes the published facts agree. The set is
    discovered by running the refresh, not guessed. The bundle may remain a
    mixture of commits; the report names the files left stale.

``--mode tree``
    carry the whole reviewed package, so the bundle's package reproduces the
    reviewed tree. This set is *enumerated*, not discovered: it is safe
    because it is proved the same way -- the refreshed copy must run and must
    publish the reviewed tree's facts before anything is written.

Two more answers, neither of which writes:

``--check``
    report the bundle's identity as one comparable observation: the sha256 of
    the bundle's package and the sha256 of the reviewed tree's package, over
    the same set of names, so the two can be compared by a reader or a runner
    without re-deriving anything. The names that differ are listed. The
    published facts are compared as well, so the answer distinguishes a
    bundle that is *current* (its bytes are the reviewed tree's), one that is
    *mixed* (its bytes differ but it still publishes the reviewed tree's
    facts) and one that is *stale* (its facts differ). A commit identity is
    not used for this answer: a commit names bytes the bundle may not carry,
    and a bundle that is a mixture of commits has no commit to name.

``--allow-mixture``
    permit a refresh that would leave the bundle without a reviewed commit
    identity. The site records which reviewed commit each off-pin root
    carries, and a bundle that no reviewed commit reproduces cannot be
    described by such a record, so the tool refuses that refresh by default
    and says which mode keeps the identity. The plant's own identity check
    answers the question; the tool asks it rather than re-deriving it.

The package is not only Python: it ships the role charters
(``seed_<slug>_charter.md``), the doctrine and the agent contract, and
``seed.py`` reads a charter out of the package directory at runtime, so those
files are carried and reported like the modules.

No refresh is written without ``--apply``. A dry run is the default.
``--revert`` writes the bytes a recorded refresh replaced, and that is the
only way besides ``--apply`` that this tool changes the bundle.

The tool executes the reviewed tree's projection code and the plant's running
bundle code against the plant's own home, read-only, to learn what each
publishes. That is the same execution the plant's own closure check performs.

Exit status:
  0  the plan is safe (dry run), the refresh was written and verified
     (apply), the previous bytes were restored (revert), or the bundle's
     published facts agree with the reviewed tree's (check)
  2  the refresh was refused: the plan cannot be made safe, the refresh failed
     its verification, the rollback could not restore every byte, or the
     refresh would leave the bundle without a reviewed commit identity
  3  the question cannot be decided: no bundle, no reviewed tree, an
     unreadable manifest, or the discovery module is unavailable
  4  the plan leaves published facts differing, and was not applied; under
     ``--check``, the bundle's published facts differ from the reviewed
     tree's
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DISCOVERY = REPO / "checks" / "observer-refresh-closure.py"
IDENTITY = REPO / "checks" / "observer-source-identity.py"
PACKAGE = "mishe_tauftauf"
STAMP = "%Y%m%dT%H%M%SZ"
CACHES = ("__pycache__",)


class Undecidable(Exception):
    """The tool lacks the evidence to decide, and says so."""


def load_discovery(path: Path = DISCOVERY):
    """The plant's own discovery, loaded by path.

    ``checks/observer-refresh-closure.py`` finds the run closure from the
    import errors a refresh produces and the fact closure from the published
    facts. Re-implementing that here would create a second convention for
    "which files must a refresh carry", and the two would drift.
    """
    try:
        spec = importlib.util.spec_from_file_location("observer_refresh_closure", path)
    except (OSError, ValueError) as exc:
        raise Undecidable("cannot read the discovery module at " + str(path)
                          + ": " + type(exc).__name__)
    if spec is None or spec.loader is None:
        raise Undecidable("the discovery module at " + str(path) + " is not loadable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ValueError, SyntaxError) as exc:
        raise Undecidable("cannot load the discovery module at " + str(path)
                          + ": " + type(exc).__name__)
    for name in ("bundle_root", "export_head", "closure", "fact_closure",
                 "evidence_lines", "fact_distance", "fact_gap"):
        if not hasattr(module, name):
            raise Undecidable("the discovery module at " + str(path)
                              + " does not provide " + name)
    return module


def load_identity(path: Path = IDENTITY):
    """The plant's own identity check, loaded by path.

    ``checks/observer-source-identity.py`` answers whether a package
    directory's bytes are reproduced by a reviewed commit. The site records
    which commit each off-pin root carries, so "which commit is this bundle"
    is a question the plant already answers; the tool asks it rather than
    keeping a second convention for it.
    """
    try:
        spec = importlib.util.spec_from_file_location("observer_source_identity", path)
    except (OSError, ValueError) as exc:
        raise Undecidable("cannot read the identity module at " + str(path)
                          + ": " + type(exc).__name__)
    if spec is None or spec.loader is None:
        raise Undecidable("the identity module at " + str(path) + " is not loadable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ValueError, SyntaxError) as exc:
        raise Undecidable("cannot load the identity module at " + str(path)
                          + ": " + type(exc).__name__)
    for name in ("module_blobs", "reproducing_commits", "Undecidable"):
        if not hasattr(module, name):
            raise Undecidable("the identity module at " + str(path)
                              + " does not provide " + name)
    return module


_IDENTITY = None


def identity_check():
    """The plant's identity check, loaded once."""
    global _IDENTITY
    if _IDENTITY is None:
        _IDENTITY = load_identity()
    return _IDENTITY


def identity_state(repo: Path, directory: Path) -> tuple:
    """Whether a package directory's bytes are reproduced by a reviewed commit.

    "identity" means at least one reviewed commit reproduces the directory's
    module bytes, so the site can name a commit for the root. "mixed" means
    none does: the directory is a hand copy of several commits, and the
    site's declared-root record has no commit to name for it. "undecidable"
    means the question could not be asked -- no repository, no modules, an
    unreadable file -- and is not the same answer as "mixed".
    """
    try:
        module = identity_check()
    except Undecidable as exc:
        return "undecidable", str(exc)
    try:
        blobs = module.module_blobs(repo, directory)
        commits = module.reproducing_commits(repo, blobs)
    except module.Undecidable as exc:
        return "undecidable", str(exc)
    except (OSError, ValueError) as exc:
        return "undecidable", type(exc).__name__
    if not commits:
        return "mixed", "no reviewed commit reproduces these bytes"
    if len(commits) == 1:
        return "identity", sorted(commits)[0]
    return "identity", (str(len(commits)) + " reviewed commits reproduce these bytes")


def digest(path: Path) -> str:
    """The sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference_identity(repo: Path, reference: Path, modules: list,
                       from_repo: bool) -> tuple:
    """What the reviewed tree is, in a form a record can be checked against.

    The git commit when the tree came from the repository, and always a digest
    over the files' bytes: an exported tree is not a repository, and a record
    that names only a commit cannot be checked when there is none. The commit
    is never taken from the repository when the tree came from somewhere else,
    because then it would name bytes the refresh did not carry.
    """
    commit = ""
    candidates = [repo, reference] if from_repo else [reference]
    for candidate in candidates:
        try:
            result = subprocess.run(["git", "-C", str(candidate), "rev-parse", "HEAD"],
                                    capture_output=True, text=True, check=False)
        except OSError:
            continue
        if result.returncode == 0 and result.stdout.strip():
            commit = result.stdout.strip()
            break
    parts = []
    for name in sorted(modules):
        path = reference / PACKAGE / name
        if path.is_file():
            parts.append(name + ":" + digest(path))
    return commit, hashlib.sha256("\n".join(parts).encode()).hexdigest()


@dataclass
class Plan:
    """What a refresh of this bundle would do, and why."""

    bundle: Path
    reference: Path
    mode: str
    carried: list = field(default_factory=list)
    added: list = field(default_factory=list)
    replaced: list = field(default_factory=list)
    already_current: list = field(default_factory=list)
    stale_left: list = field(default_factory=list)
    only_in_bundle: list = field(default_factory=list)
    reference_lines: list = field(default_factory=list)
    distance: int = 0
    from_repo: bool = True
    notes: list = field(default_factory=list)
    identity_before: str = "undecidable"
    identity_after: str = "undecidable"
    identity_detail: str = ""
    moving: list = field(default_factory=list)

    @property
    def identity_regression(self) -> bool:
        """Whether the refresh would take the bundle's reviewed identity away.

        The site records which reviewed commit each off-pin root carries. A
        bundle that no reviewed commit reproduces cannot be described by such
        a record, and the plant's own identity check reads it as a
        difference. A refresh that creates that state is refused unless the
        caller accepts a mixture on purpose.
        """
        return self.identity_before == "identity" and self.identity_after == "mixed"

    def describe(self) -> list:
        """The plan, one line per part of it."""
        lines = ["bundle    : " + str(self.bundle),
                 "reference : " + str(self.reference),
                 "mode      : " + self.mode,
                 "carried   : " + (", ".join(self.carried) or "(nothing)"),
                 "replaced  : " + (", ".join(self.replaced) or "(none)"),
                 "added     : " + (", ".join(self.added) or "(none)"),
                 "already   : " + str(len(self.already_current)) + " file(s) already current"]
        if self.only_in_bundle:
            lines.append("only in bundle: " + ", ".join(self.only_in_bundle)
                         + " (the reviewed tree carries no such file)")
        if self.stale_left:
            lines.append("left stale: " + ", ".join(self.stale_left)
                         + " (the planned set publishes the reviewed tree's facts without them)")
        if self.identity_regression:
            lines.append("identity  : WOULD BE LOST — the bundle now carries a reviewed "
                         "commit and this refresh would leave it with none")
        for note in self.notes:
            lines.append("note      : " + note)
        return lines


def package_files(root: Path) -> list:
    """Every file the package directory carries, except compiled caches.

    The package is not only Python: it ships the role charters, the doctrine
    and the agent contract, and ``seed.py`` reads a charter out of the package
    directory at runtime.
    """
    directory = root / PACKAGE
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(directory).parts
        if any(part in CACHES for part in parts) or path.suffix == ".pyc":
            continue
        out.append(path.relative_to(directory).as_posix())
    return out


def package_digest(root: Path, names: list) -> str:
    """The sha256 of a package's bytes over a fixed set of names.

    Both sides are digested over the same names, and a name that is missing on
    one side is recorded as absent rather than skipped, so the two digests are
    comparable: equal digests mean equal bytes, and different digests mean the
    packages differ.
    """
    parts = []
    for name in sorted(names):
        path = root / PACKAGE / name
        if path.is_file():
            parts.append(name + ":" + digest(path))
        else:
            parts.append(name + ":absent")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


@dataclass
class Observation:
    """The bundle's bytes against the reviewed tree's bytes."""

    bundle: Path
    reference: Path
    names: list = field(default_factory=list)
    bundle_digest: str = ""
    reference_digest: str = ""
    differing: list = field(default_factory=list)
    only_in_bundle: list = field(default_factory=list)
    only_in_reference: list = field(default_factory=list)
    distance: int = 0
    facts: str = "unknown"
    facts_reason: str = ""
    moving: list = field(default_factory=list)

    def state(self) -> str:
        """The one word that names what the observation says.

        CURRENT: the bundle's bytes are the reviewed tree's bytes.
        MIXED: the bytes differ but the published facts still agree, so the
        plant publishes what the reviewed tree publishes.
        STALE: the published facts differ, so the plant publishes something
        the reviewed tree does not.
        UNKNOWN: the bytes differ and the facts could not be read.
        """
        if self.bundle_digest == self.reference_digest:
            return "CURRENT"
        if self.facts == "agree":
            return "MIXED"
        if self.facts == "differ":
            return "STALE"
        return "UNKNOWN"

    def describe(self) -> list:
        """The observation, one line per part of it."""
        lines = ["bundle    : " + str(self.bundle),
                 "reference : " + str(self.reference),
                 "bundle    : sha256-" + self.bundle_digest + " (" + str(len(self.names))
                 + " name(s))",
                 "reference : sha256-" + self.reference_digest + " (" + str(len(self.names))
                 + " name(s))"]
        if self.differing:
            lines.append("bytes     : DIFFER — " + str(len(self.differing))
                         + " file(s) differ: " + ", ".join(self.differing))
        elif self.only_in_bundle:
            lines.append("bytes     : DIFFER — only in the bundle: "
                         + ", ".join(self.only_in_bundle))
        elif self.only_in_reference:
            lines.append("bytes     : DIFFER — only in the reviewed tree: "
                         + ", ".join(self.only_in_reference))
        else:
            lines.append("bytes     : SAME — the bundle's package is the reviewed tree's package")
        if self.facts == "agree":
            lines.append("facts     : AGREE — the bundle publishes the reviewed tree's facts")
        elif self.facts == "differ":
            lines.append("facts     : DIFFER — " + str(self.distance)
                         + " published fact(s) differ")
        else:
            lines.append("facts     : UNKNOWN — " + (self.facts_reason or "the facts could not be read"))
        if self.moving:
            lines.append("moved     : " + str(len(self.moving))
                         + " published line(s) moved between two samples (they carry the "
                         "plant's own live state), so they are not a difference in the bytes")
        lines.append("state     : " + self.state())
        return lines


def observe(discovery, home: Path, repo: Path, reference_src,
            workdir: Path) -> Observation:
    """What the bundle's bytes are, against the reviewed tree's bytes.

    Read-only: the plant's bundle is not written, and the reviewed tree is
    read from the repository's HEAD unless a reference is named.
    """
    try:
        bundle = discovery.bundle_root(home)
    except discovery.Undecidable as exc:
        raise Undecidable(str(exc))
    if reference_src is not None:
        reference = Path(reference_src).resolve()
    else:
        try:
            reference = discovery.export_head(repo, workdir)
        except discovery.Undecidable as exc:
            raise Undecidable(str(exc))
    if not (reference / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the reviewed tree at " + str(reference) + " carries no plant package")
    bundle_files = set(package_files(bundle))
    reference_files = set(package_files(reference))
    names = sorted(bundle_files | reference_files)
    observation = Observation(bundle=bundle, reference=reference, names=names,
                               bundle_digest=package_digest(bundle, names),
                               reference_digest=package_digest(reference, names),
                               differing=sorted(name for name in names
                                                if _bytes(bundle, name) is not None
                                                and _bytes(reference, name) is not None
                                                and _bytes(bundle, name) != _bytes(reference, name)),
                               only_in_bundle=sorted(bundle_files - reference_files),
                               only_in_reference=sorted(reference_files - bundle_files))
    try:
        distance, moving = sampled_distance(discovery, bundle, reference, home, repo)
    except discovery.Undecidable as exc:
        observation.facts = "unknown"
        observation.facts_reason = str(exc)
        return observation
    observation.distance = distance
    observation.moving = moving
    observation.facts = "differ" if distance else "agree"
    return observation


def _bytes(root: Path, name: str):
    """A file's bytes under a package root, or None when it is not there."""
    path = root / PACKAGE / name
    if not path.is_file():
        return None
    return path.read_bytes()


def _stable_lines(first: list, second: list) -> list:
    """The published lines that are the same in two samples of one tree.

    A line that appears in only one of the two samples carries the plant's
    own live state -- the newest outcome refusal names a role and a file
    path -- so it cannot be compared between two trees: it is not a fact
    about the bytes.
    """
    common = Counter(first) & Counter(second)
    return list(common.elements())


def sampled_distance(discovery, root: Path, reference: Path, home: Path,
                     repo: Path) -> tuple:
    """How many published facts really differ, and which lines merely moved.

    Each tree is sampled twice, and the lines that are the same in both
    samples of that tree are the ones that describe its bytes. The trees are
    then compared on those lines, so a line that moves with the plant's own
    state is named as moved instead of being reported as a difference in the
    bytes.
    """
    published_first = discovery.evidence_lines(root, home, repo)
    expected_first = discovery.evidence_lines(reference, home, repo)
    published_second = discovery.evidence_lines(root, home, repo)
    expected_second = discovery.evidence_lines(reference, home, repo)
    stable_published = _stable_lines(published_first, published_second)
    stable_expected = _stable_lines(expected_first, expected_second)
    missing, extra = discovery.fact_gap(stable_published, stable_expected)
    moved = sorted(set(published_first) - set(stable_published))
    moved += sorted(set(expected_first) - set(stable_expected))
    return len(missing) + len(extra), sorted(set(moved))


def build_plan(discovery, home: Path, repo: Path, reference_src, mode: str,
               workdir: Path, prune: bool = False) -> Plan:
    """Discover what a refresh would carry, and prove it on a disposable copy."""
    try:
        bundle = discovery.bundle_root(home)
    except discovery.Undecidable as exc:
        raise Undecidable(str(exc))
    from_repo = reference_src is None
    if reference_src is not None:
        reference = Path(reference_src).resolve()
    else:
        try:
            reference = discovery.export_head(repo, workdir)
        except discovery.Undecidable as exc:
            raise Undecidable(str(exc))
    if not (reference / PACKAGE / "__init__.py").is_file():
        raise Undecidable("the reviewed tree at " + str(reference) + " carries no plant package")
    try:
        reference_lines = discovery.evidence_lines(reference, home, repo)
    except discovery.Undecidable as exc:
        raise Undecidable(str(exc))
    plan = Plan(bundle=bundle, reference=reference, mode=mode,
                reference_lines=list(reference_lines), from_repo=from_repo)
    notes = []
    if mode == "tree":
        carried = package_files(reference)
        notes.append("tree mode enumerates the reviewed package; its safety is proved "
                     "by running the refreshed copy, not by discovery")
    else:
        try:
            run_closure = discovery.closure(bundle, reference, workdir, home, repo)
            facts = discovery.fact_closure(bundle, reference, workdir, home, repo,
                                           run_closure, reference_lines)
        except discovery.Undecidable as exc:
            raise Undecidable(str(exc))
        carried = list(facts["carried"])
        if facts["distance"]:
            notes.append("the discovered set still leaves " + str(facts["distance"])
                         + " published fact(s) differing")
        if facts["base_distance"] and not facts["added"]:
            notes.append("the run closure alone leaves " + str(facts["base_distance"])
                         + " published fact(s) differing and no single module closes them")
    plan.carried = sorted(carried)
    plan.notes = notes
    bundle_files = set(package_files(bundle))
    reference_files = set(package_files(reference))
    carried_set = set(plan.carried)
    for name in plan.carried:
        source = reference / PACKAGE / name
        target = bundle / PACKAGE / name
        if not source.is_file():
            continue
        if name not in bundle_files:
            plan.added.append(name)
        elif source.read_bytes() != target.read_bytes():
            plan.replaced.append(name)
        else:
            plan.already_current.append(name)
    plan.only_in_bundle = sorted(bundle_files - reference_files)
    plan.stale_left = sorted(name for name in bundle_files & reference_files
                             if name not in carried_set
                             and (reference / PACKAGE / name).read_bytes()
                             != (bundle / PACKAGE / name).read_bytes())
    # Prove the plan on a disposable copy: the evidence leg must run and must
    # publish the reviewed tree's facts. The plant's own bundle is not touched.
    copy = workdir / "planned"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(bundle, copy)
    for name in plan.carried:
        source = reference / PACKAGE / name
        if source.is_file():
            target = copy / PACKAGE / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, target)
    if prune:
        for name in plan.only_in_bundle:
            (copy / PACKAGE / name).unlink(missing_ok=True)
    try:
        distance, moving = sampled_distance(discovery, copy, reference, home, repo)
    except discovery.Undecidable as exc:
        raise Undecidable("the planned refresh does not run: " + str(exc))
    plan.distance = distance
    plan.moving = moving
    if moving:
        plan.notes.append(str(len(moving)) + " published line(s) moved between two samples "
                          "(they carry the plant's own live state), so they are not a "
                          "difference in the bytes")
    # The refreshed copy must still have a reviewed identity: the site names a
    # reviewed commit for this root, and a bundle that no commit reproduces
    # cannot be described by that name. The plant's own identity check answers
    # the question, on the copy, before anything is written.
    plan.identity_before, detail_before = identity_state(repo, bundle / PACKAGE)
    plan.identity_after, detail_after = identity_state(repo, copy / PACKAGE)
    if plan.identity_regression:
        plan.identity_detail = ("the bundle now carries the reviewed commit "
                                + detail_before + " and this refresh would leave it with "
                                "none (" + detail_after + "); --mode tree keeps the identity")
        plan.notes.append(plan.identity_detail)
    return plan


def refresh_root(home: Path) -> Path:
    """Where a refresh's backups and manifests live."""
    return home / "artifacts" / "observer-bundle-refresh"


def write_activation(bundle: Path, record: dict, stamp: str) -> Path:
    """The activation record the plant's observer directory keeps.

    The record names the bytes that are now running, so the site can answer
    "which commit are these bytes" without re-deriving it. The name carries the
    commit when there is one and the reference's digest when there is not.
    """
    directory = bundle.parent
    directory.mkdir(parents=True, exist_ok=True)
    identity = (str(record.get("commit") or "")[:7]
                or ("sha256-" + str(record.get("reference_digest", ""))[:7]))
    target = directory / ("activation-" + stamp + "-refresh-" + identity + ".json")
    target.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return target


def restore(backup: Path, files: list, removed: list, bundle: Path) -> list:
    """Put the bundle's previous bytes back, and say what could not be put back.

    Every restored file is compared with the digest the manifest recorded for
    it, and a backup copy that is missing is a failure rather than a silent
    skip: a rollback that half works leaves a bundle nobody can describe.
    """
    problems = []
    for entry in files:
        target = bundle / PACKAGE / entry["module"]
        if entry.get("before_sha256") is None:
            target.unlink(missing_ok=True)
            continue
        source = backup / "before" / entry["module"]
        if not source.is_file():
            problems.append(entry["module"] + ": no backup copy to restore from")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        if digest(target) != entry["before_sha256"]:
            problems.append(entry["module"] + ": the restored bytes do not match the "
                            "digest the manifest recorded")
    for entry in removed:
        source = backup / "before" / entry["module"]
        target = bundle / PACKAGE / entry["module"]
        if not source.is_file():
            problems.append(entry["module"] + ": no backup copy to restore from")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        if digest(target) != entry["before_sha256"]:
            problems.append(entry["module"] + ": the restored bytes do not match the "
                            "digest the manifest recorded")
    return problems


def apply_plan(discovery, plan: Plan, home: Path, repo: Path, workdir: Path,
               prune: bool = False) -> dict:
    """Write the refresh, verify it, and roll it back if it fails."""
    stamp = datetime.now(timezone.utc).strftime(STAMP)
    root = refresh_root(home)
    root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=stamp + "-", dir=str(root)))
    (backup / "before").mkdir(parents=True, exist_ok=True)
    files = []
    removed = []
    try:
        for name in plan.carried:
            source = plan.reference / PACKAGE / name
            target = plan.bundle / PACKAGE / name
            if not source.is_file():
                continue
            entry = {"module": name, "action": "replace" if target.is_file() else "add",
                     "after_sha256": digest(source)}
            if target.is_file():
                entry["before_sha256"] = digest(target)
                keep = backup / "before" / name
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(target, keep)
            else:
                entry["before_sha256"] = None
            files.append(entry)
        if prune:
            for name in plan.only_in_bundle:
                target = plan.bundle / PACKAGE / name
                if not target.is_file():
                    continue
                removed.append({"module": name, "before_sha256": digest(target)})
                keep = backup / "before" / name
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(target, keep)
        commit, reference_digest = reference_identity(repo, plan.reference, plan.carried,
                                                      plan.from_repo)
        record = {"version": 1, "created": datetime.now(timezone.utc).isoformat(),
                  "kind": "observer-bundle-refresh", "bundle": str(plan.bundle),
                  "reference": str(plan.reference), "commit": commit,
                  "reference_digest": reference_digest, "mode": plan.mode,
                  "carried": plan.carried, "files": files, "removed": removed,
                  "manifest": str(backup / "manifest.json")}
        # The manifest is written before the first byte is copied: a failure
        # between the write and the record must still leave a route back.
        (backup / "manifest.json").write_text(
            json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        for entry in files:
            source = plan.reference / PACKAGE / entry["module"]
            target = plan.bundle / PACKAGE / entry["module"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, target)
            entry["after_sha256"] = digest(target)
        for entry in removed:
            (plan.bundle / PACKAGE / entry["module"]).unlink(missing_ok=True)
        try:
            distance, moving = sampled_distance(discovery, plan.bundle, plan.reference,
                                                home, repo)
        except discovery.Undecidable as exc:
            problems = restore(backup, files, removed, plan.bundle)
            return _outcome("ROLLBACK FAILED" if problems else "ROLLED BACK", backup,
                            "the refreshed bundle does not run: " + str(exc), problems)
        if distance:
            problems = restore(backup, files, removed, plan.bundle)
            return _outcome("ROLLBACK FAILED" if problems else "ROLLED BACK", backup,
                            "the refreshed bundle still publishes " + str(distance)
                            + " fact(s) the reviewed tree does not", problems)
        record["files"] = files
        if moving:
            record["moving_lines"] = moving
        activation = write_activation(plan.bundle, record, stamp)
        record["activation"] = str(activation)
        (backup / "manifest.json").write_text(
            json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        problems = restore(backup, files, removed, plan.bundle)
        return _outcome("ROLLBACK FAILED" if problems else "ROLLED BACK", backup,
                        "the refresh could not be written: " + type(exc).__name__, problems)
    return {"state": "REFRESHED", "manifest": str(backup / "manifest.json"),
            "activation": str(activation), "backup": str(backup),
            "files": len(files), "removed": len(removed)}


def _outcome(state: str, backup: Path, reason: str, problems: list) -> dict:
    """The result of a refresh that was undone."""
    outcome = {"state": state, "reason": reason, "backup": str(backup)}
    if problems:
        outcome["problems"] = problems
    return outcome


def revert(manifest: Path) -> dict:
    """Restore the bytes a refresh replaced, from its manifest."""
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Undecidable("the manifest at " + str(manifest) + " is unreadable: "
                          + type(exc).__name__)
    if not isinstance(record, dict) or "bundle" not in record:
        raise Undecidable("the manifest at " + str(manifest) + " names no bundle")
    backup = manifest.parent
    bundle = Path(record["bundle"])
    files = record.get("files", [])
    removed = record.get("removed", [])
    problems = restore(backup, files, removed, bundle)
    for entry in files:
        target = bundle / PACKAGE / entry["module"]
        if entry.get("before_sha256") is None:
            if target.exists():
                problems.append(entry["module"] + ": was added and is still present")
            continue
        if not target.is_file():
            problems.append(entry["module"] + ": is missing")
        elif digest(target) != entry["before_sha256"]:
            problems.append(entry["module"] + ": does not match its recorded digest")
    for entry in removed:
        target = bundle / PACKAGE / entry["module"]
        if not target.is_file():
            problems.append(entry["module"] + ": was removed and was not restored")
        elif digest(target) != entry["before_sha256"]:
            problems.append(entry["module"] + ": was not restored to its recorded digest")
    if problems:
        return {"state": "INCOMPLETE", "problems": problems, "backup": str(backup)}
    return {"state": "REVERTED", "files": len(files), "removed": len(removed),
            "backup": str(backup)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, required=True, help="site home of the plant")
    parser.add_argument("--repo", type=Path, default=None,
                        help="reviewed repository (default: the checkout holding the home)")
    parser.add_argument("--reference-src", type=Path, default=None,
                        help="import root of the reviewed tree (default: a clean HEAD export)")
    parser.add_argument("--mode", choices=("facts", "tree"), default="facts",
                        help="facts: the minimal set that makes the published facts agree; "
                             "tree: the whole reviewed package")
    parser.add_argument("--apply", action="store_true",
                        help="write the refresh (the default is a dry run)")
    parser.add_argument("--prune", action="store_true",
                        help="remove the files the reviewed tree does not carry")
    parser.add_argument("--revert", type=Path, default=None, metavar="MANIFEST",
                        help="restore the bytes a refresh replaced")
    parser.add_argument("--check", action="store_true",
                        help="report the bundle's identity against the reviewed tree's, "
                             "read-only, and write nothing")
    parser.add_argument("--allow-mixture", action="store_true",
                        help="permit a refresh that would leave the bundle without a "
                             "reviewed commit identity")
    args = parser.parse_args(argv)
    home = args.home.resolve()
    repo = (args.repo or home.parent).resolve()
    if args.revert is not None:
        try:
            result = revert(args.revert.resolve())
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        print("state     : " + result["state"])
        for problem in result.get("problems", []):
            print("problem   : " + problem)
        print("backup    : " + result.get("backup", ""))
        return 0 if result["state"] == "REVERTED" else 2
    workdir = Path(tempfile.mkdtemp(prefix="refresh-observer-bundle-"))
    try:
        discovery = load_discovery()
        if args.check:
            try:
                observation = observe(discovery, home, repo, args.reference_src, workdir)
            except Undecidable as exc:
                print("UNKNOWN — " + str(exc))
                return 3
            for line in observation.describe():
                print(line)
            state = observation.state()
            if state == "STALE":
                return 4
            if state == "UNKNOWN":
                return 3
            return 0
        try:
            plan = build_plan(discovery, home, repo, args.reference_src, args.mode,
                              workdir, prune=args.prune)
        except Undecidable as exc:
            print("UNKNOWN — " + str(exc))
            return 3
        for line in plan.describe():
            print(line)
        if plan.distance:
            print("facts     : DIFFER — the planned refresh still publishes "
                  + str(plan.distance) + " fact(s) the reviewed tree does not")
            return 4
        print("facts     : AGREE — the planned refresh publishes the reviewed tree's facts")
        if plan.identity_regression and not args.allow_mixture:
            print("identity  : REFUSED — " + plan.identity_detail)
            print("           pass --mode tree to keep the bundle's reviewed commit, "
                  "or --allow-mixture to accept a bundle with no reviewed commit")
            return 2
        if not args.apply:
            print("apply     : no — this was a dry run; pass --apply to write it")
            return 0
        result = apply_plan(discovery, plan, home, repo, workdir, prune=args.prune)
        print("state     : " + result["state"])
        if result.get("reason"):
            print("reason    : " + result["reason"])
        for problem in result.get("problems", []):
            print("problem   : " + problem)
        if result.get("manifest"):
            print("manifest  : " + result["manifest"])
        if result.get("activation"):
            print("activation: " + result["activation"])
        if result.get("backup"):
            print("backup    : " + result["backup"])
        return 0 if result["state"] == "REFRESHED" else 2
    except Undecidable as exc:
        print("UNKNOWN — " + str(exc))
        return 3
    except (OSError, ValueError, KeyError) as exc:
        print("FAILED — the refresh could not be completed: " + type(exc).__name__
              + ": " + str(exc)[:200])
        print("backup    : the plant's bundle may hold a mixed state; the backup path is "
              "under " + str(refresh_root(home)))
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
