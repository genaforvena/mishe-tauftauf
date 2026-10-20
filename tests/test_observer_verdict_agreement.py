"""The verdict-agreement detector must fail on the pre-fix reader bytes.

On 2026-10-20 the plant's substrate observation read UNKNOWN for six hours
because the observer ran reader bytes older than the fix that corrected it.
``checks/observer-verdict-agreement.py`` exists to make that divergence
visible in one interval, and these tests pin its decisions:

* the same reviewed bytes on both sides read AGREE;
* the pre-fix reader bytes on the observer side read DISAGREE and name the
  faults the stale reader invents;
* a published reading that disagrees with the reviewed tree reads DISAGREE;
* an import root that cannot be read, a missing published snapshot and a stale
  published snapshot read UNKNOWN, never green.

The plant these tests observe is a synthetic site home, so the readings are
fixed by the fixture rather than by the machine: the same assertions hold in
CI and on the plant. The pre-fix bytes are the reader as committed at
``3e3f8be``, the last commit before the fix that corrected it.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "observer-verdict-agreement.py"
FIXTURE = (REPO / "tests" / "fixtures" / "observer-verdict-agreement"
           / "pre-fix-substrate_watch.py")
PRE_FIX_COMMIT = "3e3f8be"
PACKAGE = "mishe_tauftauf"

# A top-pane file that no declared window names. The pre-fix reader counted
# every top-pane file as a renderer, so it invents this one; the reviewed
# reader counts only declared windows and does not.
UNDECLARED_RENDERER = "stale-beta"


def load_check():
    """Import the check script, whose filename is not an importable module."""
    spec = importlib.util.spec_from_file_location("observer_verdict_agreement", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check():
    return load_check()


@pytest.fixture
def plant(tmp_path):
    """A synthetic site home whose substrate reading the fixture fixes."""
    home = tmp_path / "site"
    (home / "health").mkdir(parents=True)
    (home / "top-pains").mkdir()
    (home / "checks").mkdir()
    # No services: the reading must not depend on the machine's systemd.
    (home / "health" / "services.json").write_text("[]")
    (home / "health" / "windows.json").write_text(json.dumps(["alpha"]))
    (home / "top-pains" / "alpha").write_text("# a declared window\n")
    (home / "top-pains" / UNDECLARED_RENDERER).write_text(
        "# a top-pane file no declared window names\n")
    return home


def reviewed_tree(check, tmp_path):
    """The reviewed tree: a clean export of HEAD, not the working tree."""
    workdir = tmp_path / "reviewed-export"
    workdir.mkdir()
    return check.export_head(REPO, workdir)


def stale_tree(reviewed, tmp_path):
    """The reviewed tree carrying the pre-fix reader bytes."""
    target = tmp_path / "stale"
    shutil.copytree(reviewed, target)
    (target / PACKAGE / "substrate_watch.py").write_bytes(FIXTURE.read_bytes())
    return target


def publish(check, home, reference):
    """Publish the reading the reviewed tree produces, as the plant would."""
    verdict = check.read_verdict(reference, home, home)
    row = {"checked_at": datetime.now(timezone.utc).isoformat(), "runtime": verdict}
    (home / "checks" / "substrate-watch.json").write_text(json.dumps(row))
    return row


def run_check(home, observer, reference):
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home),
                           "--repo", str(REPO), "--observer-src", str(observer),
                           "--reference-src", str(reference)],
                          capture_output=True, text=True)


def test_reviewed_bytes_on_both_sides_agree(check, plant, tmp_path):
    reviewed = reviewed_tree(check, tmp_path)
    publish(check, plant, reviewed)

    result = run_check(plant, reviewed, reviewed)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "AGREE" in result.stdout
    assert "DISAGREE" not in result.stdout


def test_pre_fix_reader_bytes_disagree(check, plant, tmp_path):
    reviewed = reviewed_tree(check, tmp_path)
    stale = stale_tree(reviewed, tmp_path)
    publish(check, plant, reviewed)

    result = run_check(plant, stale, reviewed)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "DISAGREE" in result.stdout
    # The stale reader invents a renderer that no declared window names, and
    # the detector has to name it: an unnamed disagreement is not actionable.
    assert UNDECLARED_RENDERER in result.stdout
    assert "live renderer import roots unavailable: " + UNDECLARED_RENDERER in result.stdout


def test_a_disagreeing_published_reading_is_detected(check, plant, tmp_path):
    reviewed = reviewed_tree(check, tmp_path)
    row = publish(check, plant, reviewed)
    row["runtime"]["problems"] = ["live service import roots unavailable: ghost.service"]
    row["runtime"]["state"] = "UNKNOWN"
    (plant / "checks" / "substrate-watch.json").write_text(json.dumps(row))

    result = run_check(plant, reviewed, reviewed)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "DISAGREE" in result.stdout
    assert "ghost.service" in result.stdout


def test_an_import_root_without_the_plant_package_is_unknown(check, plant, tmp_path):
    reviewed = reviewed_tree(check, tmp_path)
    publish(check, plant, reviewed)
    empty = tmp_path / "no-package"
    empty.mkdir()

    result = run_check(plant, empty, reviewed)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_missing_published_snapshot_is_unknown(check, plant, tmp_path):
    reviewed = reviewed_tree(check, tmp_path)

    result = run_check(plant, reviewed, reviewed)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "no published substrate snapshot" in result.stdout


def test_a_stale_published_snapshot_is_unknown(check, plant, tmp_path):
    reviewed = reviewed_tree(check, tmp_path)
    row = publish(check, plant, reviewed)
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    row["checked_at"] = old.isoformat()
    (plant / "checks" / "substrate-watch.json").write_text(json.dumps(row))

    result = run_check(plant, reviewed, reviewed)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "stale" in result.stdout


def test_the_fixture_is_the_pre_fix_reader_bytes():
    blob = subprocess.run(["git", "-C", str(REPO), "show",
                           PRE_FIX_COMMIT + ":src/mishe_tauftauf/substrate_watch.py"],
                          capture_output=True)
    if blob.returncode:
        pytest.skip("the local history does not carry " + PRE_FIX_COMMIT)
    assert blob.stdout == FIXTURE.read_bytes()
