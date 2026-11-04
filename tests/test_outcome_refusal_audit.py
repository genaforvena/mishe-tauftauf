"""A refused outcome publication must leave a record the plant can see.

The writer's refusals are the plant's only direct evidence that the outcome
instrument denied a role. Without them a plant-wide denial reads as a plant
that produced nothing.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from mishe_tauftauf import wall
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


def test_a_refusal_records_the_evidence_argument_it_was_made_for(tmp_path):
    home = _site(tmp_path)
    evidence = _evidence(home)
    Feed(home).append("witness", "Wall outcome accepted by witness\nProse without a reference.\n")
    with pytest.raises(ValueError, match="cannot derive"):
        wall.outcome(home, "research-methods", "accepted", "A later contribution.", evidence)
    entry = _refusals(home)[0]
    assert entry["evidence"] == str(evidence)


def test_a_successful_publication_leaves_no_refusal_record(tmp_path):
    home = _site(tmp_path)
    wall.outcome(home, "research-methods", "accepted", "A contribution.", _evidence(home))
    assert _refusals(home) == []






