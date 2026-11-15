"""Tests for the Pub plant-state claims detector.

The detector answers one question: does a published figure about the
plant's own running state match the plant now?  On 2026-11-15 two posts
stated such a figure in the present tense and both were false when read
again: "the current ledger scan finds 123 rows whose original evidence is
permanently lost" (the live scan read 126) and "the repository's current
commit is 990 commits ahead of the release the running services are pinned
to" (the live gap was 3).  The account's other detectors read counts a page
publishes about the DEV account, so nothing read a figure about the plant.

Every test here names the mutation to the detector that would make it fail.
A test that cannot name one is not pinning anything.
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("pub_plant_state_claims", "checks/pub-plant-state-claims.py")

# The day the corpus's own sentence names.  A "read again on <date>" clause
# is a live claim only when that date is the day the check is read against,
# so a fixture naming a calendar day is read against that day, not against
# the wall clock: otherwise the test breaks on a date, not on a defect.
CORPUS_DATE = check.CORPUS_DATE

# The live readings the fixture plant reports, so a claim that drifts is one
# number away.
LIVE = {
    "ledger_permanent_loss": 126,
    "ledger_owed": 75,
    "pin_gap": 3,
    "producer_units_naming_release": 41,
    "checkout_head": "20c65d7309ed4c617913d5317d661912dd23ec56",
}

BODIES = {
    "1": "The current ledger scan finds 126 rows whose original evidence is permanently lost.\n",
    "2": "The repository's current commit is 3 commits ahead of the release the running services are pinned to.\n",
    "3": "Forty-one producer units still name a release directly.\n",
    "4": "The checkout is arborescent (you are on `20c65d73`).\n",
}


def _failures(bodies=None, live=None, today=None):
    return check.check(bodies if bodies is not None else BODIES,
                       live if live is not None else LIVE,
                       today=today if today is not None else CORPUS_DATE)


def _clock(day):
    """A datetime class whose now() is the given UTC day."""
    return type("FrozenDatetime", (datetime,), {
        "now": classmethod(lambda cls, tz=None: datetime(day.year, day.month,
                                                         day.day, 12, 0, 0,
                                                         tzinfo=tz))})


def test_claims_that_hold_are_quiet():
    """The detector is quiet when the plant agrees with the published figures."""
    failures, undecidable = _failures()
    assert failures == []
    assert undecidable == []


def test_a_stale_ledger_count_is_found():
    """A body that understates the ledger's lost rows is found."""
    failures, _ = _failures({"1": "The current ledger scan finds 123 rows whose "
                                 "original evidence is permanently lost.\n"})
    assert len(failures) == 1
    assert "the plant reads 126" in failures[0]


def test_a_stale_owed_count_is_found():
    """A body that understates the ledger's still-owed rows is found."""
    failures, _ = _failures({"1": "The current ledger scan finds 76 rows whose "
                                 "evidence is still owed.\n"})
    assert len(failures) == 1
    assert "the plant reads 75" in failures[0]


def test_a_stale_pin_gap_is_found():
    """A body that overstates the gap to the pinned release is found."""
    failures, _ = _failures({"2": "The repository's current commit is 990 commits "
                                 "ahead of the release the running services are "
                                 "pinned to.\n"})
    assert len(failures) == 1
    assert "the plant reads 3" in failures[0]


def test_a_stale_unit_count_is_found():
    """A body that understates the units that name a release is found."""
    failures, _ = _failures({"3": "Nineteen producer units still name a release directly.\n"})
    assert len(failures) == 1
    assert "the plant reads 41" in failures[0]


def test_a_stale_checkout_commit_is_found():
    """A body that names the wrong checkout commit is found."""
    failures, _ = _failures({"4": "The checkout is arborescent (you are on `51a5e68`).\n"})
    assert len(failures) == 1
    assert "the checkout is on 20c65d73" in failures[0]


def test_a_checkout_commit_in_the_corpus_wording_is_found():
    """The corpus says the checkout 'stands at' a commit; that is a claim too.

    The registry only knew "you are on", so the wording the corpus actually
    uses was invisible: a stale commit in that wording read GREEN.  The
    fixture is the corpus's own sentence shape.
    """
    # The run date is the detector's own rule ("<day> <Month>" of the day the
    # check reads the body against).  A hardcoded day made this fixture pass
    # only on that one date, and the wall clock as the reference made it
    # depend on the day the suite happens to run.  The corpus names 15
    # November, so the fixture names 15 November and is read against it.
    run_day = "%d %s" % (CORPUS_DATE.day, CORPUS_DATE.strftime("%B"))
    body = ("- The checkout is arborescent - it stood at `51a5e68` when this "
            "was written on 29 October, and read again on " + run_day + " it "
            "stands at `20c65d7309ed4c617913d5317d661912dd23ec56`.\n")
    failures, undecidable = _failures({"4": body})
    assert undecidable == []
    assert failures == []

    stale = body.replace("20c65d7309ed4c617913d5317d661912dd23ec56", "f5f7c10b")
    failures, _ = _failures({"4": stale})
    assert len(failures) == 1
    assert "the checkout is on 20c65d73" in failures[0]


def test_the_run_date_clause_is_a_live_claim_on_the_day_it_names(monkeypatch):
    """A "read again on <today>" clause is a live claim on the day it names.

    The check reads the wall clock when no day is given, so the sentence must
    name the day the check reads.  The check is run here with the clock
    frozen on several days: on each of them the clause is a live claim and a
    stale commit in it reads RED.
    """
    for probe in (date(2026, 11, 15), date(2026, 11, 16), date(2026, 12, 1),
                  date(2027, 1, 1)):
        monkeypatch.setattr(check, "datetime", _clock(probe))
        run_day = "%d %s" % (probe.day, probe.strftime("%B"))
        body = ("- The checkout is arborescent - it stood at `51a5e68` when this "
                "was written on 29 October, and read again on " + run_day + " it "
                "stands at `20c65d7309ed4c617913d5317d661912dd23ec56`.\n")
        stale = body.replace("20c65d7309ed4c617913d5317d661912dd23ec56", "f5f7c10b")
        failures, _ = check.check({"4": stale}, LIVE)
        assert len(failures) == 1, "stale commit read GREEN on %s" % probe
        assert "the checkout is on 20c65d73" in failures[0]


def test_a_clause_naming_another_day_is_history(monkeypatch):
    """A "read again on <another day>" clause is a dated record, not a claim.

    The corpus's own sentence names 15 November.  Read on any other day it
    reports a past reading, so it is history and the detector does not claim
    the plant is still on that commit.
    """
    monkeypatch.setattr(check, "datetime", _clock(date(2026, 11, 20)))
    body = ("- The checkout is arborescent - it stood at `51a5e68` when this "
            "was written on 29 October, and read again on 15 November it "
            "stands at `20c65d7309ed4c617913d5317d661912dd23ec56`.\n")
    failures, _ = check.check({"4": body}, LIVE)
    assert failures == []


def test_a_figure_the_sentence_dates_is_not_a_claim():
    """A figure its own sentence places in the past is a reading, not a claim."""
    failures, _ = _failures({"1": "On 11 November, the ledger scan found 123 rows "
                                 "whose original evidence is permanently lost.\n"})
    assert failures == []


def test_a_figure_in_the_past_tense_is_not_a_claim():
    """A figure the sentence reports in the past tense is not a live claim."""
    failures, _ = _failures({"2": "The gap was 3 commits ahead of the release the "
                                 "running services are pinned to.\n"})
    assert failures == []


def test_a_figure_dated_by_the_paragraph_above_a_list_is_not_a_claim():
    """A paragraph that dates the list below it dates the list's figures."""
    failures, _ = _failures({"3": "Checked against the running system on 11 November.\n\n"
                                 "- Nineteen producer units still name a release directly.\n"})
    assert failures == []


def test_a_figure_dated_by_the_section_heading_is_not_a_claim():
    """A heading that carries a date dates the section's figures."""
    failures, _ = _failures({"3": "## Checked on 11 November\n\n"
                                 "- Nineteen producer units still name a release directly.\n"})
    assert failures == []


def test_a_figure_inside_a_correction_notice_is_not_a_claim():
    """A correction notice's numbers are history, not a claim."""
    failures, _ = _failures({"1": "> **Correction, 2026-11-15.** It said \"the current "
                                 "ledger scan finds 123 rows whose original evidence is "
                                 "permanently lost\".\n"})
    assert failures == []


def test_an_update_line_is_not_a_notice_so_its_live_figure_is_checked():
    """An 'Update:' opener is an ordinary sentence; its live figure is a claim."""
    failures, _ = _failures({"1": "Update: the current ledger scan finds 123 rows whose "
                                 "original evidence is permanently lost.\n"})
    assert failures and "123" in failures[0]


def test_a_spaced_compound_unit_count_is_read_whole():
    """'Forty one producer units' is 41, not 'one': the whole compound is read."""
    failures, _ = _failures({"3": "Forty one producer units still name a release directly.\n"})
    assert failures == []
    failures, _ = _failures({"3": "Thirty one producer units still name a release directly.\n"})
    assert failures and "41" in failures[0]


def test_a_checkout_head_compares_regardless_of_case():
    """An upper-case hex commit in the body is the same commit as the checkout."""
    head = LIVE["checkout_head"]
    failures, _ = _failures({"3": "The checkout is on %s.\n" % head[:8].upper()})
    assert failures == []


def test_a_figure_the_plant_publishes_no_reading_for_is_undecidable():
    """A reading the plant cannot supply is UNKNOWN, never a pass."""
    failures, undecidable = _failures(
        {"1": "The current ledger scan finds 123 rows whose original evidence "
              "is permanently lost.\n"},
        {"ledger_owed": 75})
    assert failures == []
    assert len(undecidable) == 1
    assert "no live reading" in undecidable[0]


def test_a_number_the_text_cannot_read_is_not_a_claim():
    """A figure that is neither a digit nor a known number word is not read."""
    assert check.live_figures("The current ledger scan finds many rows whose "
                               "original evidence is permanently lost.\n",
                               today=CORPUS_DATE) == []


def test_a_number_word_is_read():
    """A figure written as a word is read and checked."""
    failures, _ = _failures({"3": "Nineteen producer units still name a release directly.\n"})
    assert len(failures) == 1
    assert "the plant reads 41" in failures[0]


def test_the_scope_of_a_body_is_read_per_line():
    """The scope that governs a line covers the lines under it, not above."""
    body = "## Checked on 11 November\n\n- Nineteen units still name a release directly.\n"
    scope = check.scope_lines(body)
    assert "Checked on 11 November" in scope[0]
    assert "Checked on 11 November" in scope[1]
    assert "Checked on 11 November" in scope[2]
    # A scope governs what comes after it, so the paragraph above it is not
    # governed by it.
    body = "- Nineteen units still name a release directly.\n\n## Checked on 11 November\n"
    scope = check.scope_lines(body)
    assert "Checked on 11 November" not in scope[0]


def test_the_self_test_passes():
    """The check's own controlled failure runs and passes."""
    assert check._self_test() == 0


def test_the_corpus_word_order_for_landed_reviews_is_read():
    """'N landed reviews' is read as the landed count, as the corpus writes it."""
    failures, _ = _failures({"6": "383 landed reviews, 0 pending of 192 cells.\n"},
                            {"field_review_landed": 380, "field_review_pending": 0})
    assert len(failures) == 1
    assert "the plant reads 380" in failures[0]


def test_the_corpus_word_order_for_pending_cells_is_read():
    """'N of M cells are pending' is read as the pending count, as the corpus writes it."""
    failures, _ = _failures({"6": "383 reviews landed; 55 of 192 cells are pending.\n"},
                            {"field_review_landed": 383, "field_review_pending": 0})
    assert len(failures) == 1
    assert "the plant reads 0" in failures[0]


PUBLISHED = {
    "published_commits": {"20c65d7309ed4c617913d5317d661912dd23ec56",
                          "237db71f63d99e2df370418a6b599ed8537efb84"},
    "published_paths": {"docs/new-senses.md"},
    "published_refs": ["refs/remotes/origin/main"],
    # A fixture has no remote to ask, so it states what the remote would
    # answer.  The live path asks the remote itself.
    "reachable": {"20c65d7309ed4c617913d5317d661912dd23ec56",
                  "237db71f63d99e2df370418a6b599ed8537efb84"},
}
"""The published tree the fixture plant reports: two commits, one path."""


def _pointer_failures(body, published=PUBLISHED):
    live = dict(LIVE)
    live["source_pointer"] = published
    return check.check({"1": body}, live, today=CORPUS_DATE)


def test_a_pointer_to_a_commit_no_published_ref_reaches_is_found():
    """A source pointer whose commit no published ref reaches is a failure.

    This is the defect three live posts carried until 2026-11-15: each said
    the direction it answers is public at `docs/new-senses.md` (commit
    `35f7cb4`), and that commit was gone from the public repository, so a
    reader who followed the pointer got a 404.  The registry knew nothing
    about pointers, so the corpus read GREEN.
    """
    failures, undecidable = _pointer_failures(
        "The direction is public at `docs/new-senses.md` (commit `35f7cb4`).\n")
    assert failures and "35f7cb4" in failures[0]
    assert "no published ref" in failures[0]
    assert undecidable == []


def test_a_pointer_to_a_path_the_public_repository_does_not_carry_is_found():
    """A pointer to a path the public repository lacks is a failure.

    The retired repository's paths are not published here, so a pointer that
    sends a reader to one of them leads nowhere either.
    """
    failures, _ = _pointer_failures(
        "The direction is public at `docs/reviews` (commit `20c65d73`).\n")
    assert failures and "docs/reviews" in failures[0]
    assert "does not carry" in failures[0]


def test_a_pointer_that_resolves_is_quiet():
    """A pointer the public repository can satisfy reads GREEN."""
    failures, undecidable = _pointer_failures(
        "The direction is public at `docs/new-senses.md` (commit `237db71f`).\n")
    assert failures == []
    assert undecidable == []


def test_a_bare_backticked_id_is_not_a_pointer():
    """A backticked hex string that is not a source pointer is not a claim.

    The corpus is full of them and they mean different things: a process id
    ("rows from `41945` and `4176129`"), the head of the retired repository
    ("`d67bd016`"), and ids a post itself says are no longer reachable.  A
    rule that read every one of them as a commit failed on the corpus it was
    measured against, which is how a detector stops being acted on.
    """
    failures, undecidable = _pointer_failures(
        "Rows from `41945` and `4176129` in the same table; this box wraps around four million.\n")
    assert failures == []
    assert undecidable == []


def test_a_pointer_the_plant_cannot_read_is_undecidable():
    """A published tree the plant cannot read is UNKNOWN, never a pass."""
    live = dict(LIVE)
    live["source_pointer"] = None
    failures, undecidable = check.check(
        {"1": "The direction is public at `docs/new-senses.md` (commit `35f7cb4`).\n"},
        live, today=CORPUS_DATE)
    assert failures == []
    assert len(undecidable) == 1
    assert "could not be read" in undecidable[0]


def test_a_clone_that_lacks_the_published_head_is_undecidable():
    """No local tree for the remote head: UNKNOWN, not a pass and not a failure.

    The remote is the authority on reachability, but paths are read from the
    clone.  A clone that does not hold the remote head cannot say whether the
    path exists there, and guessing either way would be a false verdict.
    """
    published = dict(PUBLISHED)
    published["published_paths"] = None
    failures, undecidable = _pointer_failures(
        "The direction is public at `docs/new-senses.md` (commit `237db71f`).\n",
        published)
    assert failures == []
    assert len(undecidable) == 1
    assert "does not hold the published head" in undecidable[0]


def test_published_tree_with_missing_first_head_keeps_other_claims_readable(monkeypatch, tmp_path):
    def git(repo, *args):
        if args[0] == "ls-remote":
            return 0, "aaa\trefs/heads/unmirrored\nbbb\trefs/heads/main\n"
        if args[0] == "rev-parse":
            return (1, "") if "unmirrored" in args[-1] else (0, "bbb\n")
        if args[0] == "ls-tree":
            return 0, "docs/example.md\n"
        raise AssertionError(args)

    monkeypatch.setattr(check, "git", git)
    result = check.read_published_tree(tmp_path)
    assert result["published_commits"] == {"aaa", "bbb"}
    assert result["published_paths"] is None


def test_published_tree_does_not_trust_a_stale_local_ref(monkeypatch, tmp_path):
    def git(repo, *args):
        if args[0] == "ls-remote":
            return 0, "new\trefs/heads/main\n"
        if args[0] == "rev-parse":
            return 0, "old\n"
        if args[0] == "ls-tree":
            return 0, "obsolete.md\n"
        raise AssertionError(args)

    monkeypatch.setattr(check, "git", git)
    result = check.read_published_tree(tmp_path)
    assert result["published_commits"] == {"new"}
    assert result["published_paths"] is None
