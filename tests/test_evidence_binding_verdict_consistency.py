"""The verdict-consistency detector must catch both ways a verdict misleads.

``sense.ledger.evidence-binding`` publishes one state word for the plant's
outcome-evidence ledger. The detector asks a property question about that
published reading: does the state word agree with the reading's own standing
audit?

Two failure shapes are detected, and both were measured live on 2026-10-21
(research-methods, wake 77624):

* **certifies** — the reading says ``verified`` while its own audit reports
  violating rows. This is what the plant would publish the moment the one
  coverage gap that binds nothing is closed, over 211 violating rows.
* **resting on an irrelevant gap** — the reading says ``unknown`` while every
  coverage gap it lists binds no evidence path, so its indeterminacy is not
  about the ledger it measures. This is the plant's current reading.

Exit status under test:
  0  the published verdict agrees with its own standing audit
  2  the verdict certifies a damaged ledger, or rests on a gap that binds
     nothing
  3  the question cannot be decided: no reading, a stale one, or one without
     the counters the property needs
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "evidence-binding-verdict-consistency.py"


def run_check(home: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home)],
                          capture_output=True, text=True)


def write_reading(home: Path, entry: dict, *, created: str | None = None) -> None:
    """Publish a discovery sample carrying one evidence-binding reading."""
    directory = home / "discovery"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = created or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {"created": created or datetime.now(timezone.utc).isoformat(),
               "producer": "test-producer@0",
               "observations": [dict(entry, id="sense.ledger.evidence-binding")]}
    (directory / ("scan-%s-test.json" % stamp)).write_text(json.dumps(payload),
                                                             encoding="utf-8")


def reading(**overrides) -> dict:
    """A published reading, with the fields the property needs."""
    base = {
        "state": "verified",
        "all_bad": 0,
        "count_excluded": 0,
        "latest_bad": 0,
        "coverage_incomplete": 0,
        "incomplete": [],
        "violations": [],
        "read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    base.update(overrides)
    return base


def test_a_clean_ledger_reads_consistent(tmp_path):
    home = tmp_path / "home"
    write_reading(home, reading(state="verified", all_bad=0))
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "CONSISTENT" in result.stdout


def test_a_binding_relevant_gap_may_hold_the_verdict_unknown(tmp_path):
    home = tmp_path / "home"
    write_reading(home, reading(state="unknown",
                                 coverage_incomplete=1,
                                 incomplete=[{"sequence": 42, "binding": "unknown",
                                             "reason": "damaged publication"}]))
    result = run_check(home)
    assert result.returncode == 0, result.stdout
    assert "CONSISTENT" in result.stdout


def test_a_verdict_that_certifies_a_damaged_ledger_fails(tmp_path):
    home = tmp_path / "home"
    write_reading(home, reading(state="verified", all_bad=211, count_excluded=140,
                                 violations=["genome@2026-10-21T04:23:44Z:artifacts/x.md(B)"]))
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "INCONSISTENT" in result.stdout
    assert "verified" in result.stdout


def test_a_verdict_resting_on_a_gap_that_binds_nothing_fails(tmp_path):
    home = tmp_path / "home"
    write_reading(home, reading(state="unknown",
                                 all_bad=211, count_excluded=140,
                                 coverage_incomplete=1,
                                 incomplete=[{"sequence": 66107, "binding": "none",
                                             "reason": "no final record reference"}]))
    result = run_check(home)
    assert result.returncode == 2, result.stdout
    assert "INCONSISTENT" in result.stdout


def test_the_exposure_is_reported(tmp_path):
    """The reading the plant would publish once the irrelevant gap closes."""
    home = tmp_path / "home"
    write_reading(home, reading(state="unknown",
                                 all_bad=211, count_excluded=140,
                                 coverage_incomplete=1,
                                 incomplete=[{"sequence": 66107, "binding": "none",
                                             "reason": "no final record reference"}]))
    result = run_check(home)
    assert "exposure" in result.stdout
    assert "'verified'" in result.stdout


def test_the_standing_audit_is_attributed_per_role(tmp_path):
    home = tmp_path / "home"
    write_reading(home, reading(state="verified", all_bad=2, count_excluded=2,
                                 violations=["genome@2026-10-21T04:23:44Z:artifacts/x.md(B)",
                                             "genome@2026-10-21T05:23:44Z:artifacts/y.md(B)"]))
    result = run_check(home)
    assert "genome=2" in result.stdout


def test_a_missing_reading_reads_unknown(tmp_path):
    home = tmp_path / "home"
    (home / "discovery").mkdir(parents=True)
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_a_stale_reading_reads_unknown(tmp_path):
    home = tmp_path / "home"
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_reading(home, reading(state="verified", read_at=stale))
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "stale" in result.stdout


def test_a_reading_without_the_counters_reads_unknown(tmp_path):
    home = tmp_path / "home"
    write_reading(home, {"state": "verified",
                          "read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_a_sample_without_the_sense_reads_unknown(tmp_path):
    home = tmp_path / "home"
    directory = home / "discovery"
    directory.mkdir(parents=True)
    (directory / "scan-20261021T000000Z-empty.json").write_text(
        json.dumps({"created": "2026-10-21T00:00:00Z", "observations": []}),
        encoding="utf-8")
    result = run_check(home)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout
