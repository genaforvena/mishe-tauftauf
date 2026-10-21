"""The unit-import observability checker must name the units nobody watches.

A plant unit that runs the plant's own code from a hand-assembled import
root has runtime bytes that belong to no reviewed commit, and a fix committed
to the shared checkout stays inert while that unit keeps running the old
behaviour. When such a unit is also in neither the service manifest nor the
provenance record, the plant publishes no verdict about it at all: the
question is not "the bytes are stale" but "nobody is checking".

That is the measured case on 2026-10-21. The initiative escalation monitor
ran a snapshot whose bytes predate the reviewed fix that stops one unchanged
condition from being re-notified to four roles every thirty minutes, and the
plant read ``RUNTIME: DECLARED`` and ``GREEN`` throughout, because the unit
was in neither list.

These tests pin the checker's contract on a disposable repository and a
disposable site home: a unit on the pin is not this check's business, an
off-pin unit in neither list is named unobserved, an off-pin unit the plant
declares with the commit its bytes came from reads current, and the same unit
declared with a commit its bytes did not come from reads stale.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_CHECKOUT_ROOT = Path(__file__).resolve().parent.parent
_CHECKER = _CHECKOUT_ROOT / "checks" / "unit-import-observability.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("_unit_import_observability_checker", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_checker()


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture
def plant(tmp_path):
    """A disposable repository with two commits of the plant package."""
    repo = tmp_path / "plant"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "checker@example.invalid")
    _git(repo, "config", "user.name", "checker")
    package = repo / "src" / "mishe_tauftauf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "watch.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    (package / "watch.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "second")
    return repo


def _copy_package(repo, target, names=("__init__.py", "watch.py"), commit=None):
    """A hand-assembled copy of the plant package, as a snapshot root holds."""
    directory = target / "mishe_tauftauf"
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        if commit is None:
            body = (repo / "src/mishe_tauftauf" / name).read_bytes()
        else:
            body = _git(repo, "show", commit + ":src/mishe_tauftauf/" + name).encode("utf-8")
        (directory / name).write_bytes(body)
    return target


def _site_home(tmp_path, units, declared=None, pin=None):
    """A disposable site home with a manifest, a provenance record and a pin."""
    home = tmp_path / "home"
    (home / "health").mkdir(parents=True)
    (home / "health" / "services.json").write_text(json.dumps(units) + "\n", encoding="utf-8")
    record = {"version": 1, "units": declared or {}}
    (home / "health" / "declared-roots.json").write_text(json.dumps(record) + "\n", encoding="utf-8")
    release = {"version": 1, "source": str(pin), "sha": "0" * 40,
               "session": "mishe-self-development-current"}
    (home / "health" / "runtime-release.json").write_text(json.dumps(release) + "\n", encoding="utf-8")
    return home


def test_commit_blobs_reads_the_modules_under_a_path(plant):
    """The blob ids come from the modules directly under the package path.

    A reader that rejected the names because they carry the path prefix would
    find no modules at all and would call every root current.
    """
    blobs = _MODULE.commit_blobs(plant, "HEAD", "src/mishe_tauftauf")
    assert set(blobs) == {"__init__.py", "watch.py"}, blobs


def test_a_root_on_the_pin_is_not_this_checks_business(plant, tmp_path):
    """The pinned release is observed by construction, so it is not named."""
    release = tmp_path / "release"
    _copy_package(plant, release / "src")
    home = _site_home(tmp_path, [], pin=release)
    assert _MODULE.off_pin_roots([str(release / "src")],
                                  _MODULE.pinned_root(home), str(plant / "src")) == []


def test_an_off_pin_root_is_named_off_pin(plant, tmp_path):
    """A hand-assembled root is neither the pin nor the checkout."""
    bundle = tmp_path / "bundle"
    _copy_package(plant, bundle)
    home = _site_home(tmp_path, [], pin=tmp_path / "release")
    off = _MODULE.off_pin_roots([str(bundle)], _MODULE.pinned_root(home),
                                 str(plant / "src"))
    assert off == [str(bundle.resolve())], off


def test_an_unobserved_off_pin_unit_is_named(plant, tmp_path, monkeypatch):
    """A unit in neither list is unobserved, and the check fails on it."""
    bundle = tmp_path / "bundle"
    _copy_package(plant, bundle)
    home = _site_home(tmp_path, [], pin=tmp_path / "release")
    unit = "mishe-self-development-current-initiative-watch.service"
    monkeypatch.setattr(_MODULE, "live_units", lambda: [unit])
    monkeypatch.setattr(_MODULE, "unit_runs_plant_code", lambda name: True)
    monkeypatch.setattr(_MODULE, "unit_roots", lambda name: [str(bundle)])
    assert _MODULE.main(["--home", str(home), "--repo", str(plant)]) == 2


def test_a_declared_root_carrying_its_commit_reads_current(plant, tmp_path, monkeypatch):
    """A root declared with the commit its bytes came from is current."""
    first = _git(plant, "rev-list", "--max-parents=0", "HEAD").strip()
    bundle = tmp_path / "bundle"
    _copy_package(plant, bundle, commit=first)
    unit = "mishe-self-development-current-initiative-watch.service"
    home = _site_home(tmp_path, [], declared={
        unit: {"root": str(bundle), "owner": "operator", "provenance": "x",
               "reviewed_commit": first, "reason": "test", "retire_when": "never"}},
        pin=tmp_path / "release")
    monkeypatch.setattr(_MODULE, "live_units", lambda: [unit])
    monkeypatch.setattr(_MODULE, "unit_runs_plant_code", lambda name: True)
    monkeypatch.setattr(_MODULE, "unit_roots", lambda name: [str(bundle)])
    assert _MODULE.main(["--home", str(home), "--repo", str(plant)]) == 0


def test_a_declared_root_carrying_other_bytes_reads_stale(plant, tmp_path, monkeypatch):
    """A root declared with a commit its bytes did not come from is stale."""
    bundle = tmp_path / "bundle"
    _copy_package(plant, bundle)
    first = _git(plant, "rev-list", "--max-parents=0", "HEAD").strip()
    unit = "mishe-self-development-current-initiative-watch.service"
    home = _site_home(tmp_path, [], declared={
        unit: {"root": str(bundle), "owner": "operator", "provenance": "x",
               "reviewed_commit": first, "reason": "test", "retire_when": "never"}},
        pin=tmp_path / "release")
    monkeypatch.setattr(_MODULE, "live_units", lambda: [unit])
    monkeypatch.setattr(_MODULE, "unit_runs_plant_code", lambda name: True)
    monkeypatch.setattr(_MODULE, "unit_roots", lambda name: [str(bundle)])
    assert _MODULE.main(["--home", str(home), "--repo", str(plant)]) == 2


def test_a_malformed_manifest_stays_undecided(plant, tmp_path, monkeypatch):
    """A broken allowlist must not read as agreement."""
    home = _site_home(tmp_path, [])
    (home / "health" / "services.json").write_text('{"not": "a list"}', encoding="utf-8")
    monkeypatch.setattr(_MODULE, "live_units", lambda: ["x.service"])
    assert _MODULE.main(["--home", str(home), "--repo", str(plant)]) == 3
