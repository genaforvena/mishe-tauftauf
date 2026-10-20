"""The view-agreement detector must catch the evidence the plant stops printing.

On 2026-10-20 the observer bundle that produces the plant's shared
follow-through evidence block was a hand copy of the reviewed tree taken at
16:40Z. Commit 49bb564 (20:22Z) added the outcome-refusal projection to
``follow_through.py``; the bundle's copy predates it, so the plant printed no
refusal record while the reviewed tree reported 13 refused outcome publications
in the last 24 hours. The substrate verdict agreed on both sides, so the
verdict detector was blind to it.

``checks/observer-view-agreement.py`` asks the effect-level question on the
plant's own published frame, and these tests pin its decisions:

* a projection the reviewed tree publishes and the plant does not reads
  MISSING, and names the projection and the line the plant is not showing;
* a frame that carries every reviewed projection reads AGREE;
* a projection only the plant prints reads EXTRA;
* a missing, malformed, tampered or evidence-free frame, and a reference tree
  that cannot be read, read UNKNOWN — never green.

The plant is a synthetic site home and the reference is a synthetic package,
so the readings are fixed by the fixture rather than by the machine: the same
assertions hold in CI and on the plant.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "observer-view-agreement.py"
PACKAGE = "mishe_tauftauf"
SECTION = "MIND WALL — shared follow-through evidence"
SLUG = "alpha"

# The two projections of the reviewed tree, and the one the stale bundle copy
# cannot produce.
REVIEWED = ["OUTCOME WRITER REFUSALS: 13 in the last 24h (0 coverage denials)",
            "SUBSTRATE: KNOWN change checked_at=2026-10-20T23:34:50.793940+00:00"]
STALE = ["SUBSTRATE: KNOWN change checked_at=2026-10-20T23:34:50.793940+00:00"]


def write_package(root: Path, lines) -> Path:
    """A package whose projection prints the given lines."""
    package = root / PACKAGE
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "follow_through.py").write_text(
        "from pathlib import Path\n\n"
        "LINES = %r\n\n"
        "def lines(home: Path):\n    return list(LINES)\n" % (list(lines),))
    return root


def write_frame(home: Path, body, *, digest=None, slug=SLUG) -> Path:
    """A published dashboard frame carrying the given evidence block."""
    frame = "\n".join([
        "HEADLINE: GREEN — a synthetic plant",
        SECTION + "; not an authored wall",
        *body,
        "MIND WALL — edited notes; sensor truth is above",
        "notes",
    ]) + "\n"
    directory = home / "dashboards"
    directory.mkdir(parents=True, exist_ok=True)
    record = {"version": 1, "slug": slug, "home": str(home.resolve()),
              "created": "2026-10-20T23:40:00+00:00", "frame": frame, "ok": True,
              "sha256": digest if digest is not None
              else hashlib.sha256(frame.encode()).hexdigest()}
    path = directory / f"{slug}.json"
    path.write_text(json.dumps(record))
    return path


@pytest.fixture
def plant(tmp_path):
    home = tmp_path / "site"
    (home / "dashboards").mkdir(parents=True)
    (home / "top-pains").mkdir()
    return home


@pytest.fixture
def reference(tmp_path):
    return write_package(tmp_path / "reviewed", REVIEWED)


def run_check(home: Path, reference: Path, extra=()):
    """Run the check the way a runner would, and return its result."""
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--slug", SLUG,
         "--reference-src", str(reference), *extra],
        capture_output=True, text=True)


def test_missing_projection_is_reported_with_its_content(plant, reference):
    write_frame(plant, STALE)
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "MISSING" in result.stdout
    assert "OUTCOME WRITER REFUSALS" in result.stdout
    assert "13 in the last 24h" in result.stdout
    assert "EXTRA" not in result.stdout


def test_complete_evidence_reads_agree(plant, reference):
    write_frame(plant, REVIEWED)
    result = run_check(plant, reference)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AGREE" in result.stdout


def test_projection_only_the_plant_prints_reads_extra(plant, reference):
    write_frame(plant, [*REVIEWED, "GHOST: a projection the reviewed tree does not publish"])
    result = run_check(plant, reference)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "EXTRA" in result.stdout
    assert "GHOST" in result.stdout
    assert "MISSING" not in result.stdout


def test_missing_frame_reads_unknown(plant, reference):
    result = run_check(plant, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("UNKNOWN")


def test_malformed_frame_reads_unknown(plant, reference):
    (plant / "dashboards" / f"{SLUG}.json").write_text("{not json")
    result = run_check(plant, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "malformed" in result.stdout


def test_tampered_frame_reads_unknown(plant, reference):
    write_frame(plant, REVIEWED, digest="0" * 64)
    result = run_check(plant, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "digest" in result.stdout


def test_frame_without_evidence_block_reads_unknown(plant, reference):
    write_frame(plant, [])
    (plant / "dashboards" / f"{SLUG}.json").write_text(json.dumps({
        "version": 1, "slug": SLUG, "home": str(plant.resolve()),
        "created": "2026-10-20T23:40:00+00:00", "ok": True,
        "frame": "HEADLINE: GREEN\nMIND WALL — edited notes\nnotes\n",
        "sha256": hashlib.sha256(
            "HEADLINE: GREEN\nMIND WALL — edited notes\nnotes\n".encode()).hexdigest()}))
    result = run_check(plant, reference)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "no evidence block" in result.stdout


def test_unreadable_reference_tree_reads_unknown(plant, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    write_frame(plant, REVIEWED)
    result = run_check(plant, empty)
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("UNKNOWN")


def test_invalid_slug_reads_unknown(plant, reference):
    write_frame(plant, REVIEWED)
    result = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(plant), "--slug", "../escape",
         "--reference-src", str(reference)],
        capture_output=True, text=True)
    assert result.returncode == 3, result.stdout + result.stderr
