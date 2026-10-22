"""The expiry detector must read the routed window's lifetime from published fields.

``checks/coverage-window-expiry.py`` answers a question the plant's routed
review signal depends on: how long a broken outcome row stays routed, and what
the plant's own standing audit says about the rows that have already left the
routed window. The coverage reading is a rolling 24-hour window, so a row's
routed lifetime is bounded by the window, and a row that ages out of it is
still a standing violation in the ledger's audit.

These tests pin the detector's decisions on a synthetic site home, so the
readings are fixed by the fixture rather than by the machine and the same
assertions hold in CI and on the plant:

* a published broken row inside the horizon is named with the instant its
  routed visibility ends, and the check exits 2;
* a published broken row outside the horizon is named as still routed and is
  not called out;
* a standing violation published before the window start has aged out of the
  routed window, and one published inside the window has not;
* a class-D-only violation is a citation, not a gap the coverage reader could
  have produced, so it never ages out of the routed window;
* a role whose coverage reads OBSERVED while the plant's own published gate
  still owes it a row reads GREEN BY AGE;
* a published gate that owes nothing leaves no role green by age, and the
  check still reports the rows that aged out;
* when the published reading publishes no gate, the owed question is UNKNOWN
  rather than clean, and the check says so;
* a missing, unreadable or stale reading reads UNKNOWN, never green;
* a violation line the detector cannot parse is reported, never guessed at.

The detector reads published artifacts and imports nothing from the plant
package, so a change to an internal helper's signature cannot make it fail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "coverage-window-expiry.py"

WINDOW_SECONDS = 86400


def run_check(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(home), *extra],
                          capture_output=True, text=True)


def stamp(offset_seconds: float = 0.0) -> str:
    """An ISO stamp, ``offset_seconds`` in the past."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_report(home: Path, *, roles: dict | None = None, window_start: str | None = None,
                 age: float = 0.0, raw: str | None = None) -> None:
    """Publish a coverage report with the given per-role coverage and gaps."""
    directory = home / "checks"
    directory.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (directory / "initiative.json").write_text(raw, encoding="utf-8")
        return
    start = window_start or stamp(WINDOW_SECONDS)
    payload = {
        "generated_at": stamp(age),
        "window": {"start": start, "end": stamp(age), "seconds": WINDOW_SECONDS,
                   "scope": "recorded events in the last 24h"},
        "roles": roles or {},
        "outcomes": {"state": "OBSERVED", "items": []},
    }
    (directory / "initiative.json").write_text(json.dumps(payload), encoding="utf-8")


def gap(sequence: int, role: str, *, age: float = 0.0, path: str = "artifacts/a.md") -> dict:
    """One published broken outcome row."""
    return {"ref": "chat.log#%d" % sequence, "sequence": sequence, "role": role,
            "timestamp": stamp(age), "state": "UNKNOWN", "reason": "outcome evidence empty or changed",
            "evidence": {"path": path, "sha256": "0" * 64}}


def write_ledger(home: Path, violations: list[str], *, owed_detail: list | None = None,
                 owed_roles: dict | None = None, age: float = 0.0, raw: str | None = None) -> None:
    """Publish the ledger reading with the given standing violations."""
    directory = home / "discovery"
    directory.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (directory / "latest.json").write_text(raw, encoding="utf-8")
        return
    observation = {
        "id": "sense.ledger.evidence-binding",
        "state": "drift",
        "read_at": stamp(age),
        "violations": violations,
    }
    if owed_detail is not None:
        observation["owed_detail"] = owed_detail
    if owed_roles is not None:
        observation["owed_roles"] = owed_roles
    payload = {"created": stamp(age), "observations": [observation]}
    (directory / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def site(home: Path) -> None:
    """An empty site home."""
    home.mkdir(parents=True, exist_ok=True)


def test_a_broken_row_inside_the_horizon_is_called_out(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"genome": {"outcome_coverage": "PARTIAL",
                                             "outcome_gaps": [gap(77622, "genome", age=WINDOW_SECONDS - 1800)]}})
    write_ledger(tmp_path, [])
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout
    assert "EXPIRING" in result.stdout
    assert "genome chat.log#77622" in result.stdout


def test_a_broken_row_outside_the_horizon_is_still_routed(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"genome": {"outcome_coverage": "PARTIAL",
                                             "outcome_gaps": [gap(77622, "genome", age=3600)]}})
    write_ledger(tmp_path, [])
    result = run_check(tmp_path, "--horizon-hours", "0.5")
    assert result.returncode == 0, result.stdout
    assert "EXPIRING" not in result.stdout
    assert "ROUTED" in result.stdout


def test_a_violation_published_before_the_window_has_aged_out(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"discover": {"outcome_coverage": "OBSERVED", "outcome_gaps": []}})
    write_ledger(tmp_path, ["discover@%s:artifacts/old.md(B)" % stamp(WINDOW_SECONDS + 60),
                             "discover@%s:artifacts/new.md(B)" % stamp(60)])
    result = run_check(tmp_path)
    assert "1 of them aged out of the routed window" in result.stdout
    assert "artifacts/old.md" in result.stdout
    assert "artifacts/new.md" not in result.stdout


def test_a_citation_does_not_age_out_of_the_routed_window(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"discover": {"outcome_coverage": "OBSERVED", "outcome_gaps": []}})
    write_ledger(tmp_path, ["discover@%s:artifacts/cited.md(D)" % stamp(WINDOW_SECONDS + 60)])
    result = run_check(tmp_path)
    assert "0 of them aged out of the routed window" in result.stdout


def test_a_role_the_gate_still_owes_is_green_by_age(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"discover": {"outcome_coverage": "OBSERVED", "outcome_gaps": []}})
    write_ledger(tmp_path, ["discover@%s:artifacts/old.md(B)" % stamp(WINDOW_SECONDS + 60)],
                 owed_detail=[{"sequence": 75512, "role": "discover", "classes": "B",
                               "band": "owed", "evidence": "artifacts/old.md"}],
                 owed_roles={"discover": 1})
    result = run_check(tmp_path)
    assert result.returncode == 2, result.stdout
    assert "GREEN BY AGE" in result.stdout
    assert "discover" in result.stdout


def test_a_gate_that_owes_nothing_leaves_no_role_green_by_age(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"discover": {"outcome_coverage": "OBSERVED", "outcome_gaps": []}})
    write_ledger(tmp_path, ["discover@%s:artifacts/old.md(B)" % stamp(WINDOW_SECONDS + 60)],
                 owed_detail=[], owed_roles={})
    result = run_check(tmp_path)
    assert "GREEN BY AGE" not in result.stdout
    assert "the published gate owes none of them" in result.stdout


def test_without_a_published_gate_the_owed_question_is_unknown(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"discover": {"outcome_coverage": "OBSERVED", "outcome_gaps": []}})
    write_ledger(tmp_path, ["discover@%s:artifacts/old.md(B)" % stamp(WINDOW_SECONDS + 60)])
    result = run_check(tmp_path)
    assert "gate      : UNPUBLISHED" in result.stdout
    assert "UNKNOWN" in result.stdout
    assert "GREEN BY AGE" not in result.stdout


def test_a_missing_reading_reads_unknown(tmp_path: Path) -> None:
    site(tmp_path)
    result = run_check(tmp_path)
    assert result.returncode == 3, result.stdout
    assert "UNKNOWN" in result.stdout


def test_a_stale_reading_reads_unknown(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={}, age=7200)
    write_ledger(tmp_path, [], age=7200)
    result = run_check(tmp_path)
    assert result.returncode == 3, result.stdout
    assert "stale" in result.stdout


def test_an_unparsable_violation_line_is_reported(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, roles={"discover": {"outcome_coverage": "OBSERVED", "outcome_gaps": []}})
    write_ledger(tmp_path, ["not a violation line"])
    result = run_check(tmp_path)
    assert "could not be parsed" in result.stdout


def test_a_malformed_reading_never_raises(tmp_path: Path) -> None:
    site(tmp_path)
    write_report(tmp_path, raw="{not json")
    write_ledger(tmp_path, [])
    result = run_check(tmp_path)
    assert result.returncode == 3, result.stdout
    assert result.stderr == ""
