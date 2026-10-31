"""Decoders for the reset-origin git text (``reflog_boundary``). Pure, no git."""

from mishe_tauftauf import reflog_boundary as rb


def test_reflog_keeps_only_head_resets_in_order():
    text = (
        "aaa|HEAD@{100}|reset: moving to HEAD\n"
        "bbb|HEAD@{90}|commit: second\n"
        "ccc|HEAD@{80}|reset: moving to HEAD~1\n"
        "ddd|HEAD@{70}|reset: moving to HEAD\n"
    )
    assert rb.decode_reflog_resets(text) == [(100, "aaa"), (70, "ddd")]


def test_reflog_missing_epoch_on_a_reset_is_malformed():
    assert rb.decode_reflog_resets("aaa|HEAD|reset: moving to HEAD\n") is None


def test_reflog_empty_is_no_resets():
    assert rb.decode_reflog_resets("") == []


def test_unreachable_extracts_shas_and_ignores_other_kinds():
    text = (
        "unreachable commit 1111111111111111111111111111111111111111\n"
        "unreachable blob 2222222222222222222222222222222222222222\n"
        "dangling commit 3333333333333333333333333333333333333333\n"
        "unreachable commit 4444444444444444444444444444444444444444\n"
    )
    assert rb.decode_unreachable_commits(text) == [
        "1111111111111111111111111111111111111111",
        "4444444444444444444444444444444444444444",
    ]


def test_unreachable_line_without_a_sha_is_malformed():
    assert rb.decode_unreachable_commits("unreachable commit\n") is None


def test_stash_keeps_subject_pipes_and_skips_non_stash_and_parentless():
    text = (
        "s1|p1|1700|WIP on main: a|b\n"
        "s2|p2|1701|On feature: x\n"
        "c3|p3|1702|ordinary commit\n"
        "r4||1703|WIP on main: root without parent\n"
    )
    assert rb.decode_stash_commits(text) == [
        (1700, "p1", "s1"),
        (1701, "p2", "s2"),
    ]


def test_stash_with_non_digit_timestamp_is_malformed():
    assert rb.decode_stash_commits("s1|p1|soon|WIP on main: x\n") is None


def test_empty_inputs_decode_to_empty_values():
    assert rb.decode_unreachable_commits("") == []
    assert rb.decode_stash_commits("") == []
