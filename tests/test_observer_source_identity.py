"""The observer-source checker must name the identity of a live import root.

A plant service that runs a hand-assembled copy of the plant package has
runtime bytes that belong to no reviewed commit. Nothing in the plant can
then say which source produced the running behaviour, and a fix committed to
the shared checkout stays unobserved while the service keeps reporting the old
verdict. The substrate observer is exactly that case, so the checker exists to
make the condition decidable rather than silent.

These tests pin the checker's contract on a disposable repository: a root that
reproduces one commit is named, a root that mixes two commits is named as
mixed, a root that trails HEAD is named as stale, and a root with no
repository behind it stays undecided rather than green.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_CHECKOUT_ROOT = Path(__file__).resolve().parent.parent
_CHECKER = _CHECKOUT_ROOT / "checks" / "observer-source-identity.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("_observer_source_identity_checker", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_checker()


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _write_module(repo, name, body):
    target = repo / "src" / "mishe_tauftauf" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


@pytest.fixture
def plant(tmp_path):
    """A disposable repository with two commits of the plant package.

    Two modules change, one in each commit, so a tree that takes the first
    commit's version of one and the second commit's version of the other
    matches no commit at all.
    """
    repo = tmp_path / "plant"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "checker@example.invalid")
    _git(repo, "config", "user.name", "checker")
    _write_module(repo, "__init__.py", "")
    _write_module(repo, "watch.py", "VALUE = 1\n")
    _write_module(repo, "gate.py", "VALUE = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    _write_module(repo, "watch.py", "VALUE = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second")
    _write_module(repo, "gate.py", "VALUE = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "third")
    return repo


def _copy_tree(repo, target, names):
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = repo / "src" / "mishe_tauftauf" / name
        (target / name).write_bytes(source.read_bytes())
    return target


def test_a_root_that_reproduces_head_is_current(plant, tmp_path):
    """A copy of HEAD's package is named HEAD, and nothing else."""
    root = _copy_tree(plant, tmp_path / "copy" / "mishe_tauftauf",
                       ["__init__.py", "watch.py", "gate.py"])
    head = _MODULE.head_commit(plant)
    finding = _MODULE.classify_package(plant, root, head)
    assert finding["state"] == "current", finding
    assert finding["commit"] == head, finding


def test_a_tracked_package_with_an_extra_module_is_dirty(plant):
    """An untracked module in the checkout cannot be reported as current."""
    root = _write_module(plant, "extra.py", "VALUE = 3\n").parent
    finding = _MODULE.classify_package(plant, root, _MODULE.head_commit(plant))
    assert finding["state"] == "dirty", finding


def test_a_root_that_mixes_two_commits_is_mixed(plant, tmp_path):
    """Modules drawn from two commits match no reviewed tree."""
    mixed = tmp_path / "mixed" / "mishe_tauftauf"
    mixed.mkdir(parents=True)
    first = _git(plant, "rev-list", "--max-parents=0", "HEAD").strip()
    (mixed / "__init__.py").write_bytes((plant / "src/mishe_tauftauf/__init__.py").read_bytes())
    # The first commit's watch.py and the last commit's gate.py never coexisted.
    (mixed / "watch.py").write_text(_git(plant, "show", first + ":src/mishe_tauftauf/watch.py"),
                                     encoding="utf-8")
    (mixed / "gate.py").write_bytes((plant / "src/mishe_tauftauf/gate.py").read_bytes())
    head = _MODULE.head_commit(plant)
    finding = _MODULE.classify_package(plant, mixed, head)
    assert finding["state"] == "mixed", finding


def test_a_root_behind_head_is_stale(plant, tmp_path):
    """A copy of an older commit is named stale, not current."""
    first = _git(plant, "rev-list", "--max-parents=0", "HEAD").strip()
    root = tmp_path / "old" / "mishe_tauftauf"
    root.mkdir(parents=True)
    for name in ("__init__.py", "watch.py", "gate.py"):
        (root / name).write_text(_git(plant, "show", first + ":src/mishe_tauftauf/" + name),
                                 encoding="utf-8")
    head = _MODULE.head_commit(plant)
    finding = _MODULE.classify_package(plant, root, head)
    assert finding["state"] == "stale", finding
    assert finding["commit"] == first, finding


def test_a_copy_of_head_bytes_is_current_when_head_never_touched_the_package(plant, tmp_path):
    """A copy carrying HEAD's own bytes is current, even if HEAD changed nothing.

    The commit that last changed the package directory is not necessarily
    HEAD: a commit that touched other files leaves the package's bytes
    unchanged, and those bytes are still HEAD's bytes. Answering "stale"
    from the commit that last touched the directory names an identity the
    running tree does not have.
    """
    (plant / "notes.md").write_text("unrelated\n", encoding="utf-8")
    _git(plant, "add", "-A")
    _git(plant, "commit", "-qm", "notes only")
    root = _copy_tree(plant, tmp_path / "copy" / "mishe_tauftauf",
                      ["__init__.py", "watch.py", "gate.py"])
    head = _MODULE.head_commit(plant)
    finding = _MODULE.classify_package(plant, root, head)
    assert finding["state"] == "current", finding
    assert finding["commit"] == head, finding


def test_a_root_that_differs_from_head_names_the_differing_modules(plant, tmp_path):
    """A copy behind HEAD says which modules differ, not only which commit."""
    first = _git(plant, "rev-list", "--max-parents=0", "HEAD").strip()
    root = tmp_path / "old" / "mishe_tauftauf"
    root.mkdir(parents=True)
    for name in ("__init__.py", "watch.py", "gate.py"):
        (root / name).write_text(_git(plant, "show", first + ":src/mishe_tauftauf/" + name),
                                 encoding="utf-8")
    head = _MODULE.head_commit(plant)
    finding = _MODULE.classify_package(plant, root, head)
    assert finding["state"] == "stale", finding
    assert "watch.py" in finding["detail"], finding
    assert "gate.py" in finding["detail"], finding


def test_a_stale_copy_reports_modules_added_after_its_commit(plant, tmp_path):
    """A package missing newer modules names the missing HEAD files."""
    _write_module(plant, "new.py", "VALUE = 4\n")
    _git(plant, "add", "-A")
    _git(plant, "commit", "-qm", "add module")
    first = _git(plant, "rev-parse", "HEAD^").strip()
    root = tmp_path / "old" / "mishe_tauftauf"
    root.mkdir(parents=True)
    for name in ("__init__.py", "watch.py", "gate.py"):
        (root / name).write_text(
            _git(plant, "show", first + ":src/mishe_tauftauf/" + name), encoding="utf-8")
    finding = _MODULE.classify_package(plant, root, _MODULE.head_commit(plant))
    assert finding["state"] == "stale", finding
    assert "missing=1 [new.py]" in finding["detail"], finding


def test_a_root_without_a_repository_is_undecided(tmp_path):
    """Without a reviewed repository the checker does not decide."""
    with pytest.raises(_MODULE.Undecidable):
        _MODULE.head_commit(tmp_path)


def test_a_root_without_modules_is_undecided(plant, tmp_path):
    """An import root that carries no module leaves the question open."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(_MODULE.Undecidable):
        _MODULE.module_blobs(plant, empty)


def _write_record(home, units):
    home.mkdir(parents=True, exist_ok=True)
    (home / "health").mkdir(exist_ok=True)
    (home / "health" / "declared-roots.json").write_text(
        json.dumps({"version": 1, "units": units}), encoding="utf-8")


def test_a_declared_root_carrying_its_commit_is_current(plant, tmp_path):
    """A declared root that carries the commit it names is current."""
    home = tmp_path / "home"
    root = tmp_path / "bundle" / "src" / "mishe_tauftauf"
    _copy_tree(plant, root, ["__init__.py", "watch.py", "gate.py"])
    commit = _git(plant, "rev-parse", "HEAD").strip()
    _write_record(home, {"mishe-self-development-current-watch.service": {
        "root": str(tmp_path / "bundle" / "src"), "reviewed_commit": commit}})
    declared = _MODULE.declared_roots(home)
    assert declared[0][1] == str(tmp_path / "bundle" / "src"), declared
    finding = _MODULE.declared_finding(plant, root, commit)
    assert finding["state"] == "current", finding


def test_a_declared_root_that_differs_from_its_commit_is_named(plant, tmp_path):
    """A declared root carrying other bytes names the modules that differ."""
    home = tmp_path / "home"
    root = tmp_path / "bundle" / "src" / "mishe_tauftauf"
    _copy_tree(plant, root, ["__init__.py", "watch.py", "gate.py"])
    commit = _git(plant, "rev-parse", "HEAD").strip()
    (root / "gate.py").write_text("VALUE = 3\n", encoding="utf-8")
    _write_record(home, {"mishe-self-development-current-watch.service": {
        "root": str(tmp_path / "bundle" / "src"), "reviewed_commit": commit}})
    finding = _MODULE.declared_finding(plant, root, commit)
    assert finding["state"] == "differs", finding
    assert "gate.py" in finding["detail"], finding


def test_an_unreadable_provenance_record_is_undecided(tmp_path):
    """A record the check cannot read leaves the question undecided."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "health").mkdir()
    (home / "health" / "declared-roots.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(_MODULE.Undecidable):
        _MODULE.declared_roots(home)


def test_a_declared_commit_outside_the_repository_is_undecided(plant, tmp_path):
    """A declared commit the repository does not hold cannot be compared."""
    home = tmp_path / "home"
    root = tmp_path / "bundle" / "src" / "mishe_tauftauf"
    _copy_tree(plant, root, ["__init__.py", "watch.py", "gate.py"])
    _write_record(home, {"mishe-self-development-current-watch.service": {
        "root": str(tmp_path / "bundle" / "src"),
        "reviewed_commit": "0" * 40}})
    finding = _MODULE.declared_finding(plant, root, "0" * 40)
    assert finding["state"] == "unknown-commit", finding
