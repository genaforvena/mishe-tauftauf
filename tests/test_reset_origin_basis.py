"""Contract for the head-rewinds ``reset_origin`` basis.

A ``reset: moving to HEAD`` reflog entry is labelled ``stash-correlated`` when exactly one
dangling stash commit lies within two seconds of the reset and has as parent 1 the HEAD
that the reset left. Otherwise it is ``unexplained`` (no candidate stash) or ``ambiguous``
(several candidate stashes or resets compete for the evidence).

This is correlation only. A matching timestamp and parent is not a traced caller and not
proof that a stash caused the reset, so the label never says "recovered" or "explained".

A reset moving the worktree to HEAD does not by itself drop a commit; the basis classifies
the reset event, not a commit rewind. Tests that name a dropped commit assume the HEAD
commit was actually left behind by the reset.

Pure helper: ``discovery._reset_origin``. Not yet wired into the live head-rewinds read.
"""

from mishe_tauftauf import discovery

PRE_RESET_HEAD = "a47e7a4c" + "0" * 32
OTHER_HEAD = "91d53852" + "0" * 32
T_RESET = 1730_000_000  # epoch seconds, fixture value
STASH_A = "52827eee" + "0" * 32
STASH_B = "d567f9b0" + "0" * 32
STASH_C = "237db71f" + "0" * 32


def test_single_stash_at_same_second_with_matching_parent_is_stash_correlated():
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A)]
    resets = [(T_RESET, PRE_RESET_HEAD)]
    assert discovery._reset_origin(resets, stashes) == ["stash-correlated"]


def test_reset_without_any_stash_is_unexplained():
    assert discovery._reset_origin([(T_RESET, PRE_RESET_HEAD)], []) == ["unexplained"]


def test_stash_with_different_parent_does_not_match():
    stashes = [(T_RESET, OTHER_HEAD, STASH_B)]
    assert discovery._reset_origin([(T_RESET, PRE_RESET_HEAD)], stashes) == ["unexplained"]


def test_window_boundary_outside_two_seconds_does_not_match():
    for offset in (3, -3):
        stashes = [(T_RESET + offset, PRE_RESET_HEAD, STASH_A)]
        assert discovery._reset_origin([(T_RESET, PRE_RESET_HEAD)], stashes) == ["unexplained"]


def test_window_boundary_within_two_seconds_matches_both_sides():
    for offset in (2, -2):
        stashes = [(T_RESET + offset, PRE_RESET_HEAD, STASH_A)]
        assert discovery._reset_origin([(T_RESET, PRE_RESET_HEAD)], stashes) == [
            "stash-correlated"]


def test_duplicate_record_of_the_same_stash_commit_is_one_candidate():
    """The same stash sha listed twice is one stash, not an ambiguity."""
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A), (T_RESET, PRE_RESET_HEAD, STASH_A)]
    assert discovery._reset_origin([(T_RESET, PRE_RESET_HEAD)], stashes) == [
        "stash-correlated"]


def test_two_distinct_stashes_in_window_for_one_reset_is_ambiguous():
    """Equal-distance or multiple candidates must not be resolved by list order."""
    stashes = [(T_RESET - 1, PRE_RESET_HEAD, STASH_A), (T_RESET + 1, PRE_RESET_HEAD, STASH_B)]
    assert discovery._reset_origin([(T_RESET, PRE_RESET_HEAD)], stashes) == ["ambiguous"]


def test_one_stash_in_window_for_two_resets_is_ambiguous_not_assigned():
    """A single stash commit cannot be credited to two competing resets at the same head."""
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A)]
    resets = [(T_RESET, PRE_RESET_HEAD), (T_RESET + 1, PRE_RESET_HEAD)]
    assert discovery._reset_origin(resets, stashes) == ["ambiguous", "ambiguous"]


def test_two_resets_and_two_stashes_in_one_window_are_ambiguous():
    """Greedy one-to-one matching would depend on list order here; the result must not."""
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A), (T_RESET + 1, PRE_RESET_HEAD, STASH_B)]
    resets = [(T_RESET, PRE_RESET_HEAD), (T_RESET + 1, PRE_RESET_HEAD)]
    expected = ["ambiguous", "ambiguous"]
    assert discovery._reset_origin(resets, stashes) == expected
    assert discovery._reset_origin(list(reversed(resets)), list(reversed(stashes))) == (
        list(reversed(expected)))


def test_result_follows_reset_order_under_permutation():
    """Permuting the input lists permutes the output labels with the resets, nothing else."""
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A), (T_RESET + 5000, OTHER_HEAD, STASH_B)]
    resets = [(T_RESET, PRE_RESET_HEAD), (T_RESET + 5000, OTHER_HEAD)]
    forward = discovery._reset_origin(resets, stashes)
    backward = discovery._reset_origin(list(reversed(resets)), list(reversed(stashes)))
    assert forward == ["stash-correlated", "stash-correlated"]
    assert backward == list(reversed(forward))


def test_mixed_window_keeps_each_reset_independent():
    """Mirrors the 10-30 pattern: most resets have a matching stash, one does not."""
    stashes = [
        (T_RESET, PRE_RESET_HEAD, STASH_A),
        (T_RESET + 5000, OTHER_HEAD, STASH_B),
    ]
    resets = [(T_RESET, PRE_RESET_HEAD), (T_RESET + 5000, OTHER_HEAD),
              (T_RESET + 9000, STASH_C)]
    assert discovery._reset_origin(resets, stashes) == [
        "stash-correlated", "stash-correlated", "unexplained"]


def test_conflicting_metadata_for_one_stash_is_ambiguous_in_both_orders():
    """Witness 130408: same SHA with two epochs must not depend on which record came first."""
    resets = [(100, PRE_RESET_HEAD)]
    stashes = [(100, PRE_RESET_HEAD, STASH_A), (200, PRE_RESET_HEAD, STASH_A)]
    assert discovery._reset_origin(resets, stashes) == ["ambiguous"]
    assert discovery._reset_origin(resets, list(reversed(stashes))) == ["ambiguous"]


def test_conflicting_parent_metadata_for_one_stash_is_ambiguous_in_both_orders():
    resets = [(T_RESET, PRE_RESET_HEAD)]
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A), (T_RESET, OTHER_HEAD, STASH_A)]
    assert discovery._reset_origin(resets, stashes) == ["ambiguous"]
    assert discovery._reset_origin(resets, list(reversed(stashes))) == ["ambiguous"]


def test_conflicting_stash_matching_no_reset_does_not_touch_other_labels():
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A), (T_RESET + 5000, OTHER_HEAD, STASH_B),
               (T_RESET + 5000, OTHER_HEAD, STASH_B), (T_RESET + 9000, STASH_C, STASH_C)]
    resets = [(T_RESET, PRE_RESET_HEAD)]
    assert discovery._reset_origin(resets, stashes) == ["stash-correlated"]
    assert discovery._reset_origin(resets, list(reversed(stashes))) == ["stash-correlated"]


def test_exact_duplicate_records_remain_one_observation_in_both_orders():
    stashes = [(T_RESET, PRE_RESET_HEAD, STASH_A), (T_RESET, PRE_RESET_HEAD, STASH_A)]
    resets = [(T_RESET, PRE_RESET_HEAD)]
    assert discovery._reset_origin(resets, stashes) == ["stash-correlated"]
    assert discovery._reset_origin(resets, list(reversed(stashes))) == ["stash-correlated"]
