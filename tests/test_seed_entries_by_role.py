"""Grouped per-role tape reads must match the whole-tape read for every role."""

from __future__ import annotations

from pathlib import Path

from mishe_tauftauf import seed
from mishe_tauftauf.feed import FeedEntry

DIGEST = "a" * 64
ROLES = ("alpha", "beta", "gamma")


def _entry(seq: int, body: str, source: str = "seed") -> FeedEntry:
    return FeedEntry(sequence=seq, timestamp="2026-11-02T12:00:00Z", source=source, body=body)


def _tape() -> list[FeedEntry]:
    bodies = [
        (1, "seed wake alpha observation=1"),
        (2, f"seed observation alpha sha256={DIGEST}"),
        (3, "seed yield alpha wake=1"),
        (4, "seed wake beta observation=2"),
        (5, f"seed observation beta sha256={DIGEST}"),
        (6, "seed clear alpha after=1"),
        (7, "seed wake gamma observation=3 task=x"),
        (8, "plain chat line from a peer"),
        (9, "seed yield beta wake=4 continue=1"),
        (10, f"seed observation gamma sha256={DIGEST}"),
        (11, "seed wake alpha observation=4"),
        (12, f"seed observation alpha sha256={DIGEST}"),
        (13, "seed yield alpha wake=11"),
    ]
    tape = [_entry(seq, body) for seq, body in bodies]
    # Same-looking lifecycle text in another source must never route to a role.
    tape.append(_entry(14, "seed wake beta observation=9", source="chat"))
    return tape


def test_grouped_state_matches_whole_tape_state_for_every_role() -> None:
    tape = _tape()
    buckets = seed.entries_by_role(tape, ROLES)
    for role in ROLES:
        whole = seed._state(Path("/unused"), role, entries=tape)
        grouped = seed._state(Path("/unused"), role, entries=buckets[role])
        assert grouped == whole, role


def test_each_bucket_holds_only_entries_its_role_reads() -> None:
    buckets = seed.entries_by_role(_tape(), ROLES)
    assert [e.sequence for e in buckets["alpha"]] == [1, 2, 3, 6, 11, 12, 13]
    assert [e.sequence for e in buckets["beta"]] == [4, 5, 9]
    assert [e.sequence for e in buckets["gamma"]] == [7, 10]


def test_roles_without_entries_get_empty_buckets() -> None:
    buckets = seed.entries_by_role([], ROLES)
    assert buckets == {role: [] for role in ROLES}
    assert seed._state(Path("/unused"), "alpha", entries=buckets["alpha"]) == (
        None, None, None, None, None, None, None, None)
