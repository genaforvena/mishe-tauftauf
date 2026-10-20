"""The wall-outcome coverage guard: a gap that binds nothing must not block.

The guard exists so that one evidence file is never bound to two outcomes.
A damaged tape entry that carries neither a reserved header nor an immutable
reference binds no evidence path, so it stays a reported coverage gap without
blocking every role's publication. A damaged entry that can still hide a
binding keeps failing closed.
"""
import hashlib
from datetime import datetime, timezone

import pytest

from mishe_tauftauf import records, wall
from mishe_tauftauf.feed import Feed
from mishe_tauftauf.outcome_events import classify_outcomes
from tests.test_wall import setup_wall

CLAUSE = datetime(2026, 10, 6, 16, 22, 11, tzinfo=timezone.utc)


def _site(tmp_path):
    home = tmp_path / "site"
    home.mkdir()
    setup_wall(home)
    (home / "records").mkdir()
    (home / "artifacts").mkdir()
    return home


def _evidence(home, name="probe.txt"):
    path = home / "artifacts" / name
    path.write_text("probe evidence\n")
    return path


def test_prefix_shaped_message_does_not_block_a_publication(tmp_path):
    """A plain message that only starts with the reserved prefix binds nothing."""
    home = _site(tmp_path)
    entry = Feed(home).append(
        "witness",
        "Wall outcome hypothesis-changed by witness # a plain message that only "
        "starts with the reserved prefix and carries no reference\n")
    coverage = classify_outcomes(home, CLAUSE)
    assert [row["sequence"] for row in coverage["incomplete"]] == [entry.sequence]
    assert coverage["incomplete"][0]["binding"] == "none"

    wall.outcome(home, "research-methods", "accepted", "A later contribution.", _evidence(home))

    after = classify_outcomes(home, CLAUSE)
    assert [row["sequence"] for row in after["events"]] == [entry.sequence + 1]
    assert [row["sequence"] for row in after["incomplete"]] == [entry.sequence]


def test_header_shaped_publication_without_a_reference_still_blocks(tmp_path):
    """A reserved header without its immutable reference may hide a binding."""
    home = _site(tmp_path)
    Feed(home).append("witness", "Wall outcome accepted by witness\nSome prose.\n")
    coverage = classify_outcomes(home, CLAUSE)
    assert coverage["incomplete"][0]["binding"] == "unknown"
    with pytest.raises(ValueError, match="cannot derive"):
        wall.outcome(home, "research-methods", "accepted", "A later contribution.",
                     _evidence(home))


def test_reference_bearing_damaged_publication_still_blocks(tmp_path):
    """A publication whose record cannot be read may hide a binding."""
    home = _site(tmp_path)
    first = _evidence(home, "first.txt")
    wall.outcome(home, "research-methods", "accepted", "First contribution.", first)
    second = _evidence(home, "second.txt")
    heading = "Wall outcome accepted by witness"
    data = {"role": "witness", "kind": "accepted", "text": "Second contribution.",
            "evidence": {"path": str(second),
                         "sha256": hashlib.sha256(second.read_bytes()).hexdigest()}}
    event = Feed(home).append_record("witness", heading + "\n" + data["text"], data,
                                     kind="wall-outcome")
    digest = records.REFERENCE_RE.fullmatch(event.body.splitlines()[-1]).group(1)
    (home / "records" / (digest + ".json")).unlink()

    coverage = classify_outcomes(home, CLAUSE)
    assert [row["sequence"] for row in coverage["incomplete"]] == [event.sequence]
    assert coverage["incomplete"][0]["binding"] == "unknown"
    with pytest.raises(ValueError, match="cannot derive"):
        wall.outcome(home, "senses", "accepted", "Third contribution.",
                     _evidence(home, "third.txt"))


def test_the_same_evidence_path_is_still_refused_after_the_narrowing(tmp_path):
    """The write-once rule survives: one file, one outcome."""
    home = _site(tmp_path)
    evidence = _evidence(home)
    wall.outcome(home, "research-methods", "accepted", "First contribution.", evidence)
    with pytest.raises(ValueError, match="already bound"):
        wall.outcome(home, "senses", "accepted", "Second contribution.", evidence)
