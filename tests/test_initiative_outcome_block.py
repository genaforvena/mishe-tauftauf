"""The projection must say when the outcome writer refuses every role.

"none recorded" is ambiguous: a role may have attempted and been refused, or
may not have attempted. The projection therefore reports the writer's own
guard state, over the whole tape rather than the 24 hour window, so a gap
older than the window still shows.
"""
from datetime import datetime, timedelta, timezone

from mishe_tauftauf import initiative
from mishe_tauftauf.feed import FeedEntry

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def _home(tmp_path):
    for name in ("patches", "records", "artifacts"):
        (tmp_path / name).mkdir()
    return tmp_path


def _entry(sequence, source, body, *, age=60):
    return FeedEntry(sequence, (NOW - timedelta(seconds=age)).isoformat(), source, body)


def test_a_binding_relevant_gap_is_reported_as_a_blocked_instrument(tmp_path):
    """A reserved header without its reference can still hide a binding."""
    home = _home(tmp_path)
    tape = [
        _entry(1, "seed", "seed wake discover observation=1", age=200),
        _entry(2, "witness", "Wall outcome accepted by witness\nProse without a reference.\n", age=100000),
    ]
    report = initiative.collect(home, now=NOW, entries=tape)
    assert report["outcome_block"]["state"] == "BLOCKED"
    assert [row["sequence"] for row in report["outcome_block"]["gaps"]] == [2]
    # The gap is older than the 24 hour window, so only the whole-tape check
    # can see it: the window-scoped gap list stays empty.
    assert report["outcomes"]["gaps"] == []
    lines = initiative._lines(report)
    assert any("OUTCOME INSTRUMENT BLOCKED" in line and "chat.log#2" in line for line in lines)
    assert any("outcome-block=chat.log#2" in line for line in lines)


def test_a_gap_that_binds_nothing_does_not_block_the_instrument(tmp_path):
    """A message that only starts with the reserved prefix binds no path."""
    home = _home(tmp_path)
    tape = [
        _entry(1, "seed", "seed wake discover observation=1", age=200),
        _entry(2, "witness", "Wall outcome hypothesis-changed by witness # a plain message\n",
               age=60),
    ]
    report = initiative.collect(home, now=NOW, entries=tape)
    assert report["outcome_block"]["state"] == "CLEAR"
    assert report["outcome_block"]["gaps"] == []
    # The gap is still a reported coverage gap; only the block is gone.
    assert [row["sequence"] for row in report["outcomes"]["gaps"]] == [2]
    lines = initiative._lines(report)
    assert not any("OUTCOME INSTRUMENT BLOCKED" in line for line in lines)


def test_a_plant_without_gaps_reports_a_clear_instrument(tmp_path):
    home = _home(tmp_path)
    tape = [_entry(1, "seed", "seed wake discover observation=1", age=200)]
    report = initiative.collect(home, now=NOW, entries=tape)
    assert report["outcome_block"]["state"] == "CLEAR"
