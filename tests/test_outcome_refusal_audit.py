"""A refused outcome publication must leave a record the plant can see.

The writer's refusals are the plant's only direct evidence that the outcome
instrument denied a role. Without them a plant-wide denial reads as a plant
that produced nothing.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from mishe_tauftauf import follow_through, wall
from mishe_tauftauf.feed import Feed
from tests.test_wall import setup_wall

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def _site(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    setup_wall(home)
    (home / "records").mkdir()
    (home / "artifacts").mkdir()
    return home


def _evidence(home, name="probe.txt"):
    path = home / "artifacts" / name
    if path.exists():
        # A bound evidence file is made read-only at the bind boundary, so a
        # deliberate re-write restores write access first.
        path.chmod(0o600)
    path.write_text("probe evidence\n")
    return path


def _refusals(home):
    path = home / wall.OUTCOME_REFUSAL_AUDIT
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_blocked_publication_is_audited_with_the_gap_that_blocked_it(tmp_path):
    home = _site(tmp_path)
    Feed(home).append("witness", "Wall outcome accepted by witness\nProse without a reference.\n")
    with pytest.raises(ValueError, match="cannot derive"):
        wall.outcome(home, "research-methods", "accepted", "A later contribution.",
                     _evidence(home))
    entries = _refusals(home)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["role"] == "research-methods"
    assert entry["kind"] == "accepted"
    assert "cannot derive bound evidence paths" in entry["reason"]
    assert [row["sequence"] for row in entry["gaps"]] == [1]
    assert entry["ts"]


def test_a_successful_publication_leaves_no_refusal_record(tmp_path):
    home = _site(tmp_path)
    wall.outcome(home, "research-methods", "accepted", "A contribution.", _evidence(home))
    assert _refusals(home) == []


def test_the_projection_reports_a_refusal_with_the_blocking_entry(tmp_path):
    home = _site(tmp_path)
    Feed(home).append("witness", "Wall outcome accepted by witness\nProse without a reference.\n")
    with pytest.raises(ValueError, match="cannot derive"):
        wall.outcome(home, "research-methods", "accepted", "A later contribution.",
                     _evidence(home))
    line = follow_through._outcome_refusal_lines(home, now=NOW)[0]
    assert line.startswith("OUTCOME WRITER REFUSALS: 1 in the last 24h (1 coverage denials)")
    assert "research-methods/accepted" in line
    assert "chat.log#1" in line


def test_the_projection_separates_an_ordinary_refusal_from_a_coverage_denial(tmp_path):
    home = _site(tmp_path)
    wall.outcome(home, "research-methods", "accepted", "A contribution.", _evidence(home))
    with pytest.raises(ValueError, match="already bound"):
        wall.outcome(home, "senses", "accepted", "The same evidence again.", _evidence(home))
    line = follow_through._outcome_refusal_lines(home, now=NOW)[0]
    assert "1 in the last 24h (0 coverage denials)" in line
    assert "already bound" in line


def test_the_projection_reports_no_refusals_without_claiming_the_instrument_works(tmp_path):
    home = _site(tmp_path)
    line = follow_through._outcome_refusal_lines(home, now=NOW)[0]
    assert "none recorded" in line
