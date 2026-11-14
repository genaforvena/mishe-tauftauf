"""Tests for the Pub account-count claims detector.

The detector answers one question: does a count the account publishes about
itself match the account?  On 2026-11-14 the live article 4894878 published
"this account has published 78 posts" while the account held 95, and its card
said 69; article 4949922 published "the published blog this account maintains
is 62 posts, 58 distinct titles" while the account held 95 and 91.  Both were
published and both were the author's own.  The checks that existed read the
index page's correction head and the count pages' first correction sentence,
so all three of those numbers passed.

Every test here names the mutation to the detector that would make it fail.  A
test that cannot name one is not pinning anything.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("pub_account_count_claims", "checks/pub-account-count-claims.py")

# The fixture account holds three published posts and two distinct titles, so
# a claim that drifts is one number away.
PUBLISHED = [
    {"id": 1, "title": "A post", "url": "https://dev.to/x/a-post-aaa"},
    {"id": 2, "title": "A post", "url": "https://dev.to/x/a-post-aaa"},
    {"id": 3, "title": "B post", "url": "https://dev.to/x/b-post-bbb"},
]
BODIES = {
    "1": "This account has published 3 posts, this one included.\n",
    "2": "This account has published 3 posts, this one included.\n",
    "3": "This account has published 3 posts, this one included.\n",
}
DESCRIPTIONS = {"1": "This account has published 3 posts, this one included."}


def _failures(published=None, bodies=None, descriptions=None):
    return check.check(
        PUBLISHED if published is None else published,
        BODIES if bodies is None else bodies,
        DESCRIPTIONS if descriptions is None else descriptions,
    )


def test_a_claim_that_holds_is_quiet():
    """The detector is quiet when the page and the account agree.

    A detector that reported every corpus would be noise, and its verdict
    would be ignored -- the failure mode that leaves a real defect in place.
    """
    assert _failures() == []


def test_a_stale_account_total_is_found():
    """A body that understates the account's post count is found.

    This is the defect that was live on 2026-11-14: article 4894878 said
    seventy-eight while the account held ninety-five.
    """
    failures = _failures(bodies=dict(
        BODIES, **{"3": "This account has published 2 posts, this one included.\n"}))
    assert len(failures) == 1
    assert "3" in failures[0]
    assert "2 published posts" in failures[0]
    assert "holds 3" in failures[0]


def test_a_stale_card_description_is_found():
    """A card description that understates the count is found.

    The card is what a reader sees in a link preview, and it is frozen at
    creation: a corrected body does not recompute it.  Article 4894878's card
    said sixty-nine while its body said seventy-eight.
    """
    failures = _failures(descriptions={"1": "This account has published 2 posts, this one included."})
    assert len(failures) == 1
    assert "card description" in failures[0]


def test_a_stale_distinct_title_count_is_found():
    """A body that understates the account's distinct-title count is found.

    Article 4949922 published "62 posts, 58 distinct titles" while the
    account held 95 posts and 91 titles.  The fixture account holds three
    posts and two distinct titles, so the drifted figure is one.
    """
    failures = _failures(bodies=dict(
        BODIES,
        **{"3": "The published blog this account maintains is **3 posts, "
                "3 distinct titles** (four duplicate pairs).\n"}))
    assert len(failures) == 1
    assert "distinct titles" in failures[0]
    assert "the account holds 2" in failures[0]


def test_a_dated_figure_is_not_a_claim():
    """A line that dates its own figure is not a claim about today.

    Article 4949802 published "this account has published 61 posts at the
    time this draft was written".  The figure is dated, so it is history and
    not a claim about the account today.  A detector that flags it would cry
    wolf on the corpus it is measured against.
    """
    failures = _failures(bodies=dict(
        BODIES,
        **{"3": "The count-correction history of this blog -- this account has "
                "published **2 posts** at the time this draft was written -- is "
                "itself the ledger.\n"}))
    assert failures == []


def test_a_stale_count_beside_a_dated_note_is_found():
    """A dated note on the line must not swallow a live figure on that line.

    Article 4949922's bullet carried the account's size and the note about
    when it was read on one line: "The published blog this account maintains
    is 95 posts, 91 distinct titles.  The count above was read at dashboard
    time on 2026-10-30 and read again against the live account on 2026-11-14."
    The note says the figure was read again against the account as it is
    now, so the figure is a claim about today.  Reading the qualifier as a
    property of the whole line skipped the figure, so mutating it to a stale
    count still read GREEN -- the false negative this test pins.
    """
    failures = _failures(bodies=dict(
        BODIES,
        **{"3": "The published blog this account maintains is **2 posts, 2 distinct "
                "titles**.  The count above was read at dashboard time on "
                "2026-10-30 and read again against the live account on "
                "2026-11-14.\n"}))
    assert len(failures) == 1
    assert "holds 2 published posts" in failures[0]


def test_a_stale_title_count_beside_a_dated_note_is_found():
    """The distinct-title half of a figure survives a dated note on the line.

    Dropping the titles half would let a half-corrected pair through: the
    post count can be right while the title count is not.
    """
    failures = _failures(bodies=dict(
        BODIES,
        **{"3": "The published blog this account maintains is **3 posts, 4 distinct "
                "titles**.  The count above was read at dashboard time on "
                "2026-10-30 and read again against the live account on "
                "2026-11-14.\n"}))
    assert len(failures) == 1
    assert "holds 4 distinct titles" in failures[0]


def test_a_dated_figure_the_line_reverifies_is_a_claim():
    """A figure re-read against the live account is a claim about today.

    The bullet says the count was read at dashboard time AND read again
    against the live account.  The second read is a claim about now, so a
    stale figure there is drift even though the same sentence also dates the
    earlier read.
    """
    claims = check.claims_in_line(
        "The published blog this account maintains is **3 posts, 2 distinct "
        "titles**.  The count above was read at dashboard time on 2026-10-30 "
        "and read again against the live account on 2026-11-14.")
    assert claims == [("total with titles", 3, 2)]


def test_a_figure_dated_by_when_it_was_read_is_not_a_claim():
    """A figure the line says when it was read is a dated figure.

    "The count above was read at dashboard time on 2026-10-30" tells a reader
    the number describes the account on that day, so it is history rather
    than a claim about today.  Flagging it would cry wolf on the corpus: the
    account's own pages are full of figures that carry their read date.
    """
    claims = check.claims_in_line(
        "The published blog this account maintains is **3 posts, 2 distinct "
        "titles**.  The count above was read at dashboard time on "
        "2026-10-30.")
    assert claims == []


def test_a_qualifier_aimed_at_another_quantity_does_not_date_this_figure():
    """A qualifier reaches only the figure it names.

    "The comment count above was read at dashboard time" is about the comment
    count, not the account's size, so the account size on that line stays a
    claim.
    """
    claims = check.claims_in_line(
        "The published blog this account maintains is **3 posts, 2 distinct "
        "titles**.  The comment count above was read at dashboard time on "
        "2026-10-30.")
    assert claims == [("total with titles", 3, 2)]


def test_a_correction_notice_is_not_a_claim():
    """A correction notice's numbers are history, not a claim.

    The corpus's own correction trails state what the account held on past
    dates on purpose.  Every one of them would be flagged otherwise.
    """
    failures = _failures(bodies=dict(
        BODIES,
        **{"3": "*Correction (2026-11-11): the account now holds 2 published "
                "posts. The 2nd post is [1](https://dev.to/x/a-post-aaa).*\n"}))
    assert failures == []


def test_a_count_about_another_fleet_is_not_a_claim():
    """A count that is not about this account is not a claim about it.

    The corpus is full of counts about the retired Mesh fleet's own posts and
    about the number of posts a check read; neither is a claim about the
    account's size today.
    """
    failures = _failures(bodies=dict(
        BODIES,
        **{"3": "The retired fleet had 40 posts, this one included.\n"}))
    assert failures == []


def test_a_claim_in_a_body_with_no_account_word_is_not_a_claim():
    """A post count without a word naming the account is not about it.

    "The duplicate audit now asks for a page size and reports fifty-seven
    posts read" is a count of a read, not a count of the account.
    """
    failures = _failures(bodies=dict(
        BODIES,
        **{"3": "The duplicate audit now reports 57 posts read every day.\n"}))
    assert failures == []


def test_a_pair_of_counts_is_read_as_one_claim():
    """A post count and a title count on one line are both read.

    Dropping the titles half would let the 4949922 defect through: its line
    carried both numbers, and the post count alone happened to be wrong too.
    """
    line = ("The published blog this account maintains is **3 posts, 2 distinct "
            "titles** (four duplicate pairs).\n")
    claims = check.claims_in_body(line)
    assert claims == [("total with titles", 3, 2)]


def test_the_account_total_is_read_without_a_title_count():
    """A lone post count is read as a post count."""
    claims = check.claims_in_body("This account has published 3 posts, this one included.\n")
    assert claims == [("total", 3, None)]


def test_a_word_number_is_not_read_as_a_count():
    """A count written as a word is not read.

    The corpus writes counts as words ("twenty-five posts carry a correction
    notice", "ninety-five published posts") in prose.  Reading a word as a
    number would flag prose the account never claimed was a count.
    """
    claims = check.claims_in_body(
        "Of the ninety-five published posts, forty-nine were published by the fleet.\n")
    assert claims == []


def test_an_empty_account_is_a_failure_not_a_pass():
    """An account with no published posts still reports its claims as drift.

    A corpus that cannot report a size cannot confirm a count against it: the
    live count is zero, so a claim of three posts is wrong, not unverifiable.
    """
    failures = check.check([], {"1": "This account has published 3 posts.\n"}, {})
    assert len(failures) == 1
    assert "holds 0" in failures[0]


def test_a_body_that_was_not_read_is_not_silently_skipped():
    """A body the caller handed over is checked even if it is not in the list.

    A claim that was read but not checked is the same false negative as a
    claim never read: the corpus would report GREEN while a false number sat
    in a body nobody looked at.
    """
    failures = check.check(PUBLISHED, dict(BODIES, **{
        "9": "This account has published 2 posts, this one included.\n"}), DESCRIPTIONS)
    assert len(failures) == 1
    assert "9" in failures[0]


def test_the_self_test_passes():
    """The check's own controlled failure runs and passes."""
    assert check._self_test() == 0
