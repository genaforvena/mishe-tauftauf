"""The writable-bound-evidence detector must name the rows a later edit can drop.

``checks/writable-bound-evidence.py`` reads the plant's published outcome
bindings and asks which of the bound evidence files can still be written. The
plant's clause makes a bound artifact write-once evidence: the outcome records
its digest and the standing audit keeps the row forever. The clause is
enforced on publication — the writer refuses an ineligible path, a symlink, a
traversal and a path another outcome already bound — but nothing makes the
file read-only, so the write-once property is a convention rather than a
property of the bytes.

That convention cost the plant rows: measured on 2026-10-21, nine of the
eleven non-observed rows the live coverage reader reported were artifacts
edited after they were bound, and the published bytes were permanently lost.
The coverage reader reports such a row only after the edit. This detector
reports the file that *can* be edited, which arrives first.

These tests pin the detector's decisions on synthetic sites, so the readings
are fixed by the fixture rather than by the machine:

* a bound evidence file that is writable is a finding, named with its role and
  its mode; a write-once one is not;
* a bound evidence file that no longer exists is counted as missing, not as
  writable and not as read-only;
* the two populations the check reads are reported apart: the rows the
  coverage reader classifies and the rows the record store publishes;
* the check decides from the published artifacts alone, so it imports nothing
  from the plant package and reads no tape;
* a site with no published coverage report, a malformed one, or one too old to
  decide reads UNKNOWN with a reason, never a verdict and never a traceback;
* the check writes nothing into the site it reads.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "writable-bound-evidence.py"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _site(root: Path, *, writable: int = 1, readonly: int = 1, missing: int = 0,
          report_rows: int = 0, generated_at: str | None = None,
          report: object | None = None) -> Path:
    """A synthetic site with published outcome bindings at known modes."""
    (root / "records").mkdir(parents=True, exist_ok=True)
    (root / "checks").mkdir(parents=True, exist_ok=True)
    bindings = []
    for index in range(writable):
        target = root / ("writable-%d.md" % index)
        target.write_text("work\n", encoding="utf-8")
        os.chmod(target, 0o664)
        bindings.append((target, "inference-research"))
    for index in range(readonly):
        target = root / ("readonly-%d.md" % index)
        target.write_text("work\n", encoding="utf-8")
        os.chmod(target, 0o444)
        bindings.append((target, "witness"))
    for index in range(missing):
        bindings.append((root / ("gone-%d.md" % index), "docs"))
    for index, (target, role) in enumerate(bindings):
        record = {
            "version": 1,
            "kind": "wall-outcome",
            "payload": {
                "role": role,
                "kind": "accepted",
                "text": "a contribution",
                "evidence": {"path": str(target), "sha256": _digest(target)}
                if target.is_file() else {"path": str(target), "sha256": "0" * 64},
            },
        }
        (root / "records" / ("%064d.json" % index)).write_text(
            json.dumps(record), encoding="utf-8")
    items = []
    for index in range(report_rows):
        target = root / ("report-%d.md" % index)
        target.write_text("work\n", encoding="utf-8")
        os.chmod(target, 0o664)
        items.append({"role": "genome", "evidence": {"path": str(target),
                                                     "sha256": _digest(target)}})
    stamp = generated_at if generated_at is not None else (
        datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    payload = report if report is not None else {
        "version": 1,
        "generated_at": stamp,
        "outcomes": {"items": items},
    }
    (root / "checks" / "initiative.json").write_text(
        json.dumps(payload), encoding="utf-8")
    return root


def _run(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), *extra],
        capture_output=True, text=True, timeout=120,
        cwd=str(REPO),
    )


def test_writable_bound_file_is_a_finding(tmp_path):
    home = _site(tmp_path / "site", writable=1, readonly=1)
    result = _run(home)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "writable  : 1" in result.stdout
    assert "read-only : 1" in result.stdout
    assert "inference-research=1" in result.stdout
    assert "still writable" in result.stdout


def test_write_once_bound_file_is_not_a_finding(tmp_path):
    home = _site(tmp_path / "site", writable=0, readonly=2)
    result = _run(home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "every bound evidence file is read-only" in result.stdout


def test_missing_bound_file_is_counted_apart(tmp_path):
    home = _site(tmp_path / "site", writable=1, readonly=0, missing=3)
    result = _run(home)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "missing   : 3" in result.stdout


def test_the_two_populations_are_reported_apart(tmp_path):
    home = _site(tmp_path / "site", writable=1, readonly=0, report_rows=2)
    result = _run(home)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "evidence path(s) the coverage reader names" in result.stdout
    assert "evidence path(s) the record store names" in result.stdout


def test_missing_report_reads_unknown(tmp_path):
    home = _site(tmp_path / "site")
    (home / "checks" / "initiative.json").unlink()
    result = _run(home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout.startswith("UNKNOWN")
    assert "Traceback" not in result.stderr


def test_malformed_report_reads_unknown(tmp_path):
    home = _site(tmp_path / "site")
    (home / "checks" / "initiative.json").write_text("{not json", encoding="utf-8")
    result = _run(home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "malformed" in result.stdout
    assert "Traceback" not in result.stderr


def test_stale_report_reads_unknown(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    home = _site(tmp_path / "site", generated_at=old)
    result = _run(home)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "old" in result.stdout
    assert "Traceback" not in result.stderr


def test_the_check_reads_published_artifacts_only():
    """The check must not import the plant package or decode the tape.

    A second decoder would be the second decider the plant's rulings forbid,
    and a check that asserts on an internal helper's signature died silently
    once already when that helper's arity changed.
    """
    source = CHECK.read_text(encoding="utf-8")
    assert "import mishe_tauftauf" not in source
    assert "from mishe_tauftauf" not in source
    assert "chat.log" not in source
    assert "records/" in source and "checks/initiative.json" in source


def test_the_check_writes_nothing_into_the_site(tmp_path):
    home = _site(tmp_path / "site", writable=1, readonly=1)
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    _run(home)
    after = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    assert before == after
