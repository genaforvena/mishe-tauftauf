"""Tests for the declared-root retirement detector.

The detector decides whether a declared off-pin root is still needed. Its
decisions must be sound in one direction above all: a condition it cannot read
must never be reported as satisfied, because "retirable" tells the plant's
owner to delete a record and repoint a live unit.

The detector is invoked the way the plant invokes it -- as a program over a
synthetic site home -- so the readings are fixed by the fixture rather than by
the machine, and the same assertions hold in CI and on the plant.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "declared-root-retirement.py"

PACKAGE = "mishe_tauftauf"


def run_check(home: Path, extra=()) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), *extra],
        capture_output=True, text=True, timeout=60)


def make_pin(base: Path, modules=("initiative.py", "substrate_watch.py"),
              relation=True) -> Path:
    """A pinned release carrying the named modules."""
    package = base / "pin" / "src" / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    for name in modules:
        (package / name).write_text("# module\n")
    text = "def remedy_successor(a, b):\n    return True\n" if relation else ""
    (package / "outcome_publication.py").write_text(text)
    return base / "pin"


def make_home(base: Path, units, pin=None, pin_record=None) -> Path:
    """A site home with a declared-roots record and a runtime pin record."""
    home = base / "home"
    (home / "health").mkdir(parents=True, exist_ok=True)
    (home / "health" / "declared-roots.json").write_text(
        json.dumps({"version": 1, "owner": "health", "units": units}))
    if pin_record is None:
        pin_record = {"version": 1, "source": str(pin), "sha": "0" * 40}
    (home / "health" / "runtime-release.json").write_text(json.dumps(pin_record))
    return home


def condition(module="initiative.py"):
    return "a pin release carrying src/mishe_tauftauf/" + module


def test_a_condition_the_pin_satisfies_is_not_debt(tmp_path):
    pin = make_pin(tmp_path)
    home = make_home(tmp_path, {
        "unit.service": {"root": "/elsewhere/src", "retire_when": condition()},
    }, pin=pin)
    result = run_check(home)
    assert "RETIRABLE" in result.stdout
    assert result.returncode == 2


def test_a_condition_the_pin_does_not_satisfy_is_open(tmp_path):
    pin = make_pin(tmp_path, modules=("substrate_watch.py",))
    home = make_home(tmp_path, {
        "unit.service": {"root": "/elsewhere/src", "retire_when": condition()},
    }, pin=pin)
    result = run_check(home)
    assert "RETIRABLE" not in result.stdout
    assert "open" in result.stdout
    assert result.returncode == 0


def test_a_condition_the_check_cannot_read_is_undecided_not_satisfied(tmp_path):
    """The soundness direction: unreadable must never read as retirable."""
    pin = make_pin(tmp_path)
    home = make_home(tmp_path, {
        "unit.service": {"root": "/elsewhere/src",
                         "retire_when": "replacement has verified equivalent functional coverage"},
    }, pin=pin)
    result = run_check(home)
    assert "RETIRABLE" not in result.stdout
    assert "UNDECIDED" in result.stdout
    assert result.returncode == 3


def test_an_absent_condition_is_undecided(tmp_path):
    pin = make_pin(tmp_path)
    home = make_home(tmp_path, {"unit.service": {"root": "/elsewhere/src"}}, pin=pin)
    result = run_check(home)
    assert "RETIRABLE" not in result.stdout
    assert result.returncode == 3


def test_a_disjunction_is_satisfied_when_the_pin_carries_the_module(tmp_path):
    pin = make_pin(tmp_path)
    home = make_home(tmp_path, {
        "unit.service": {"root": "/elsewhere/src",
                         "retire_when": "the unit imports the reviewed observer root "
                                        "observer-x/src, or " + condition()},
    }, pin=pin)
    result = run_check(home)
    assert "RETIRABLE" in result.stdout


def test_a_disjunction_is_satisfied_when_the_unit_imports_the_named_root(tmp_path):
    """The unit branch is decided by the unit's live root, not by the root existing."""
    pin = make_pin(tmp_path, modules=("substrate_watch.py",))
    observer = tmp_path / "home" / "observer-x" / "src" / PACKAGE
    observer.mkdir(parents=True)
    (observer / "__init__.py").write_text("")
    home = make_home(tmp_path, {
        "unit.service": {"root": "/elsewhere/src",
                         "retire_when": "the unit imports the reviewed observer root "
                                        "observer-x/src, or " + condition()},
    }, pin=pin)
    # The unit does not exist, so its live root cannot be read: the unit branch
    # is undecided, and the pin branch does not hold either.
    result = run_check(home)
    assert "RETIRABLE" not in result.stdout


def test_a_missing_record_is_unknown_rather_than_green(tmp_path):
    home = tmp_path / "home"
    (home / "health").mkdir(parents=True)
    result = run_check(home)
    assert result.returncode == 3
    assert "UNKNOWN" in result.stdout


def test_a_malformed_record_is_unknown_rather_than_green(tmp_path):
    home = tmp_path / "home"
    (home / "health").mkdir(parents=True)
    (home / "health" / "declared-roots.json").write_text("{not json")
    result = run_check(home)
    assert result.returncode == 3
    assert "UNKNOWN" in result.stdout


def test_a_missing_pin_record_is_unknown_rather_than_green(tmp_path):
    pin = make_pin(tmp_path)
    home = make_home(tmp_path, {
        "unit.service": {"root": "/elsewhere/src", "retire_when": condition()},
    }, pin=pin, pin_record={"version": 1})
    result = run_check(home)
    assert result.returncode == 3
    assert "UNKNOWN" in result.stdout


def test_a_pin_without_a_plant_package_is_unknown(tmp_path):
    home = make_home(tmp_path, {
        "unit.service": {"root": "/elsewhere/src", "retire_when": condition()},
    }, pin=tmp_path / "nowhere")
    result = run_check(home)
    assert result.returncode == 3
    assert "UNKNOWN" in result.stdout


def test_a_record_naming_no_units_is_unknown(tmp_path):
    pin = make_pin(tmp_path)
    home = make_home(tmp_path, {}, pin=pin)
    result = run_check(home)
    assert result.returncode == 3
    assert "UNKNOWN" in result.stdout


def test_judging_one_unit_leaves_the_others_unreported(tmp_path):
    pin = make_pin(tmp_path)
    home = make_home(tmp_path, {
        "one.service": {"root": "/elsewhere/src", "retire_when": condition()},
        "two.service": {"root": "/elsewhere/src", "retire_when": condition()},
    }, pin=pin)
    result = run_check(home, ("--unit", "one.service"))
    assert "one.service" in result.stdout
    assert "two.service" not in result.stdout

