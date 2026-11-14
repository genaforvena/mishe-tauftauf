"""Tests for the Pub index self-claims detector.

The detector answers one question: does a published count about the DEV
account match the account?  On 2026-11-14 the index page (DEV 4893597) said
twenty-five posts carry a correction notice within their first three lines and
the account had twenty-eight, and it called its conversation list "biggest
first" while the list was not in that order.  Both numbers were published and
both were the author's own.  Nothing checked the page against the account.

Every test here names the mutation to the detector that would make it fail.  A
test that cannot name one is not pinning anything.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("pub_index_self_claims", "checks/pub-index-self-claims.py")

# A page whose claims hold.  The numbers are the ones the fixture account
# below reports, so a claim that drifts is a mutation away.
PAGE = (
    "*Correction (2026-11-11): the account now holds 2 published posts and "
    "2 distinct titles.*\n\n"
    "Two of these posts carry a correction or update notice as the first line "
    "of their body, and 2 carry one within their first three lines.\n\n"
    "One of these posts carry a conversation: 3 comments in total, 3 of them "
    "from other authors, 1 different people\n\n"
    "Here is the whole conversation surface, biggest first:\n\n"
    "- [A post](https://dev.to/ilya_mozerov_867dbdd91feb/a-post-aaa) — 3 reader comments\n"
)

PUBLISHED = [
    {"id": 1, "title": "A post", "url": "https://dev.to/x/a-post-aaa"},
    {"id": 2, "title": "B post", "url": "https://dev.to/x/b-post-bbb"},
]
BODIES = {
    "1": "*Correction (2026-11-11): the account now holds 2 published posts.\n\nbody",
    "2": "*Correction (2026-11-11): the account now holds 2 published posts.\n\nbody",
}
THREADS = {"1": 3}


def _failures(page=PAGE, published=None, bodies=None, threads=None,
              total=3, readers=3, people=1):
    return check.check(
        page,
        PUBLISHED if published is None else published,
        BODIES if bodies is None else bodies,
        THREADS if threads is None else threads,
        total, readers, people,
    )


def test_a_page_whose_claims_hold_has_no_failure():
    """The detector is quiet when the page and the account agree.

    A detector that reported every page would be noise, and its verdict would
    be ignored — the failure mode that leaves a real defect in place.
    """
    assert _failures() == []


def test_a_stale_notice_count_is_found():
    """A page that understates the notice count is found.

    This is the defect that was live on 2026-11-14: the page said
    twenty-five and the account had twenty-eight.  A detector that missed it
    would leave the same drift in the next page.
    """
    stale = PAGE.replace("and 2 carry one within their first three lines",
                         "and 1 carry one within their first three lines")
    failures = _failures(stale)
    assert failures
    assert any("notices within the first three lines" in f for f in failures)


def test_a_stale_first_line_notice_count_is_found():
    """Both readings of the notice window are checked.

    The page names two windows — the first line of a body and the first three
    lines.  A detector that checked only one would hold while the other
    drifted, which is exactly what happened: the first-line count stayed right
    and the three-line count did not.
    """
    stale = PAGE.replace("Two of these posts carry a correction or update "
                         "notice as the first line",
                         "One of these posts carry a correction or update "
                         "notice as the first line")
    failures = _failures(stale)
    assert failures
    assert any("notices as the first line" in f for f in failures)


def test_a_stale_post_count_is_found():
    """A page that overstates the account's post count is found."""
    stale = PAGE.replace("holds 2 published posts", "holds 3 published posts")
    failures = _failures(stale)
    assert failures
    assert any("published posts" in f for f in failures)


def test_a_stale_title_count_is_found():
    """The distinct-title count is checked separately from the post count.

    Four titles on this account are published twice, so the two numbers differ
    by exactly four.  A detector that derived one from the other would be
    right about one and wrong about the other.
    """
    stale = PAGE.replace("2 distinct titles", "3 distinct titles")
    failures = _failures(stale)
    assert failures
    assert any("distinct titles" in f for f in failures)


def test_a_stale_total_comment_count_is_found():
    """The total comment count is checked against every comment.

    "98 comments in total" is the whole thread, the account owner's replies
    included; "51 of them from other authors" is the reader share of it.  The
    two are different sums, and conflating them would make the check pass a
    page that got one of them wrong.
    """
    stale = PAGE.replace("3 comments in total", "2 comments in total")
    failures = _failures(stale)
    assert failures
    assert any("comments in total" in f for f in failures)


def test_a_stale_reader_count_is_found():
    """The reader share of the comments is checked against the reader count."""
    stale = PAGE.replace("3 of them from other authors", "2 of them from other authors")
    failures = _failures(stale)
    assert failures
    assert any("reader comments" in f for f in failures)


def test_a_stale_people_count_is_found():
    """The number of distinct people is checked."""
    stale = PAGE.replace("1 different people", "2 different people")
    failures = _failures(stale)
    assert failures
    assert any("distinct people" in f for f in failures)


def test_a_stale_per_post_reader_count_is_found():
    """The per-post numbers in the conversation list are checked.

    The list names fourteen posts and a reader count for each.  A count that
    is wrong for one post is wrong for the page, and the page's totals would
    still look right.
    """
    stale = PAGE.replace("— 3 reader comments", "— 2 reader comments")
    failures = _failures(stale)
    assert failures
    assert any("reader comments on" in f for f in failures)


def test_a_list_that_is_not_in_the_order_it_claims_is_found():
    """A page that says "biggest first" and is not is found.

    This is the second defect that was live on 2026-11-14: the LoRA thread,
    with two reader comments, was printed after five threads with one each.
    Every per-post number in the list was right; the order was the only thing
    wrong, and no count could see it.
    """
    out_of_order = PAGE.replace(
        "- [A post](https://dev.to/ilya_mozerov_867dbdd91feb/a-post-aaa) — 3 reader comments",
        "- [B post](https://dev.to/ilya_mozerov_867dbdd91feb/b-post-bbb) — 1 reader comments\n"
        "- [A post](https://dev.to/ilya_mozerov_867dbdd91feb/a-post-aaa) — 3 reader comments")
    failures = _failures(out_of_order)
    assert failures
    assert any("biggest first" in f for f in failures)


def test_a_list_that_names_a_post_which_is_not_live_is_found():
    """A list entry that points at nothing is found.

    The page's list names posts; a name that resolves to no live post is a
    dead entry in the conversation surface, which is what a reader follows.
    """
    dead = PAGE.replace(
        "https://dev.to/ilya_mozerov_867dbdd91feb/a-post-aaa",
        "https://dev.to/ilya_mozerov_867dbdd91feb/a-post-that-was-never-published")
    failures = _failures(dead)
    assert failures
    assert any("not a live post" in f for f in failures)


def test_a_page_that_stops_making_a_claim_is_found():
    """A page that no longer states a count is a failure, not a pass.

    The detector answers "does the page's count match the account".  If the
    claim has gone, there is nothing to match, and reading that as a pass
    would let a page quietly stop being checked.
    """
    without = PAGE.replace(
        "One of these posts carry a conversation: 3 comments in total, 3 of them "
        "from other authors, 1 different people\n\n", "")
    failures = _failures(without)
    assert failures
    assert any("no longer states this claim" in f for f in failures)


def test_a_number_word_is_read_as_a_number():
    """The page writes its counts as words, and they are read as numbers.

    "Twenty-three" is one word with a hyphen in it, and a regex that does not
    allow the hyphen reads the claim as absent — which would make the check
    pass a page it never compared.
    """
    assert check._number("Twenty-three") == 23
    assert check._number("twenty-eight") == 28
    assert check._number("98") == 98
    assert check._number("not a number") is None


def test_a_capitalised_number_word_is_read_the_same():
    """The first word of a sentence is capitalised, and the count is the same."""
    assert check._number("Two") == check._number("two") == 2


def test_the_notice_position_is_the_content_line():
    """A notice is located by content line, not by raw line.

    Nine of the ninety-five bodies open with a blank line, so a count taken
    on raw lines would miss the notice that a reader sees first.  This is the
    difference between twenty-three and fourteen.
    """
    body = "\n\n*Correction (2026-11-11): something was wrong.\n\nThe body."
    assert check.notice_position(body) == 1
    assert check.notice_position("Just a body.") is None


def test_a_notice_on_the_second_line_is_outside_the_first_line_window():
    """The two windows really are different windows.

    A detector that used one window for both counts would report the same
    number twice and would never notice that the page's second count had
    drifted while its first had not.
    """
    body = "\n\nOpening paragraph.\n\n*Correction (2026-11-14): something.\n\nRest."
    assert check.notice_position(body) == 2
    assert check.count_notices({"1": body}, 1) == 0
    assert check.count_notices({"1": body}, 3) == 1


def test_the_self_test_passes():
    """The controlled failure the CI gate runs passes.

    The gate runs `--self-test`; a self-test that fails stops the build, so
    this is the gate on the gate.
    """
    assert check._self_test() == 0
