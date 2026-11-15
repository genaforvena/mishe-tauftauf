#!/usr/bin/env python3
"""Detector: a published figure about the plant's live state must still be true.

The gap this closes.  On 2026-11-15 two published posts stated a number
about the running plant in the present tense.  One said "the current ledger
scan finds 123 rows whose original evidence is permanently lost"; the live
ledger scan read 126.  The other said "The repository's current commit is 990
commits ahead of the release the running services are pinned to"; the live gap
was 3 commits, and 0 the next day.  Both numbers were true when they were
written.  Both were false when they were read again, and both were corrected
by hand.

Nothing read them.  The account's other detectors read the counts a page
publishes *about the DEV account* -- post counts, title counts, comment counts,
notice counts.  A published claim about the plant's own running state is a
different object, and no detector read it.  The plant could therefore publish
a stale number about itself and keep publishing it.

The rule.  A figure that names one of the plant's own readings must equal the
live value of that reading.  The readings are named in ``REGISTRY``: the
ledger's permanently-lost and still-owed rows, the gap between the pinned
release and the checkout, the producer units that name a release directly, and
the commit the checkout stands on.  Each entry names a phrase a published
figure uses, and the live value is read from the plant itself -- the newest
published discovery sample for the ledger, the pin and ``git`` for the gap
and the checkout, the installed unit files for the units.

Two things are deliberately NOT claims, because a detector that flagged them
would cry wolf on the corpus it is measured against:

* a correction notice.  A notice is a record of what the plant read on past
  dates; those numbers are history and are meant to disagree with today.
* a figure the text dates.  "On 31 October, the repository's commit was 990
  commits ahead of the release", "the ledger scan read at 05:04Z on 15
  November found 126 rows", "Checked against the running system on 11
  November" -- these report a reading, not the plant's state.  The one
  exception is a clause that says the reading was taken on the run date
  ("read again on 15 November" on 15 November): that is a live claim, and
  it does not date the figures after it.  An earlier "read again on" date
  stays history.

Scope is measured per clause, not per line and not per body.  A qualifier
dates the figure it qualifies: the figure's own clause governs it, the first
clause of its sentence governs the sentence when it carries a date, the
paragraph immediately above a list governs that list, and the heading governs
its section.  A figure whose own clause is in the past tense ("the gap was 3
commits", "the scan found 126 rows") is a record of a reading, so it is not a
claim about the plant now.

The same reasoning separates the corpus's third kind of figure.  A post that
says "at `d67bd016` the retired repository carries 377 files under
`docs/reviews/`" is not describing this plant at all.  It names a *frozen*
tree in another repository, so its figures cannot drift as the plant moves --
but they can drift when a later author corrects one and gets the tree wrong,
and nothing read them.  Nine published bodies state such figures, so the rule
is registered against that tree: each entry carries ``retired`` and is scoped
to a body that names it (``RETIRED_SCOPE``), and ``read_retired_tree`` is the
live reading.  Because a frozen tree cannot change, a past-tense verb around
one of its figures describes the tree rather than a past reading, so the
past-tense guard does not apply to these entries.  A retired-repository figure
the tree cannot answer is UNKNOWN, never a pass.

Verdicts: GREEN when every live claim matches, RED when one does not,
UNKNOWN when a reading the plant publishes cannot be read.  Exit codes:
0 GREEN, 2 RED, 3 UNKNOWN.  A detector that cannot read its input answers
UNKNOWN, never a pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"

CORPUS_DATE = date(2026, 11, 15)
"""The day the corpus's own sentence names.

The sentence the 2026-11-15 correction left in place reads "read again on
15 November it stands at `<sha>`".  A "read again on <date>" clause is a
live claim only when that date is the run date, so a fixture naming a fixed
calendar day is a live claim on that day and a dated record on every other.
The check therefore reads that sentence against this fixed date: the fixture
keeps the corpus's own shape and the check stops depending on the wall
clock.
"""

SENSE_ID = "sense.ledger.evidence-binding"
"""The plant's own published reading of the evidence ledger."""

LEDGER_MAX_AGE_S = 3600.0
"""How old a published ledger reading may be and still describe the plant.

The ledger counter moves about once a day, so an hour is generous.  A
sample older than this is not used: comparing a published figure against a
stale counter would report drift where there is only an old sample.
"""

NOTICE = re.compile(r"^\s*(>\s*)?\**\s*(correction|earlier correction)\b", re.I)
"""A correction notice.  Its numbers are history, not a claim.

Only a correction opens a notice.  An "Update:" or "Superseded:" line is an
ordinary sentence, and a figure in it about the plant now is a claim; the
reviewer's finding on release f5f7c10b showed that matching those words hid
live claims from the detector.
"""

BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
"""A list item."""

MONTHS = ("January|February|March|April|May|June|July|August|September|"
          "October|November|December")

DATE_TOKEN = re.compile(
    r"\b(?:\d{1,2}\s+(?:%s)(?:\s+\d{4})?|(?:%s)\s+\d{1,2}(?:,?\s+\d{4})?|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?Z)\b" % (MONTHS, MONTHS),
    re.I)
"""A date or a clock reading.  A figure next to one is a dated reading."""

PAST = re.compile(
    r"\b(?:was|were|had|has been|have been|read|found|counted|stood|sat|"
    r"named|says|said|then|originally|previously|until|as of|at the time|"
    r"at that time|when (?:this|it) was (?:written|published))\b", re.I)
"""A past-tense or provenance phrase.  The figure it sits in is history."""

CURRENT = re.compile(
    r"read again against the live|re-read against the live|"
    r"verified against the running|read against the running", re.I)
"""A phrase that says a figure was read against the plant as it is now."""

WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty-one": 21, "twenty-two": 22, "twenty-three": 23, "twenty-four": 24,
    "twenty-five": 25, "twenty-six": 26, "twenty-seven": 27, "twenty-eight": 28,
    "twenty-nine": 29, "thirty": 30, "thirty-one": 31, "thirty-two": 32,
    "thirty-three": 33, "thirty-four": 34, "thirty-five": 35,
    "forty": 40, "forty-one": 41, "forty-two": 42, "forty-three": 43,
    "forty-four": 44, "forty-five": 45, "forty-six": 46, "forty-seven": 47,
    "forty-eight": 48, "forty-nine": 49, "fifty": 50,
}
for _tens_name, _tens in (("sixty", 60), ("seventy", 70), ("eighty", 80), ("ninety", 90)):
    WORDS[_tens_name] = _tens
    for _unit_name, _unit in (("one", 1), ("two", 2), ("three", 3), ("four", 4), ("five", 5),
                              ("six", 6), ("seven", 7), ("eight", 8), ("nine", 9)):
        WORDS["%s-%s" % (_tens_name, _unit_name)] = _tens + _unit
"""Number words the posts use for the figures they state (up to ninety-nine)."""

RETIRED_SCOPE = re.compile(
    r"\bretired\b|d67bd016|lte-workstation", re.I)
"""How a body says it is talking about the retired repository.

A figure about a tree that has stopped moving is only a retired-repository
figure when the sentence, its clause, or the paragraph or section around it
names that tree.  Without this bound the rule would read any "N files under
`docs/reviews/`" anywhere in the corpus as a claim about a tree no post is
discussing -- the same overreach that made the broad hex rule cry wolf.
"""

REGISTRY = (
    {
        "name": "ledger-permanent-loss",
        "pattern": re.compile(r"([0-9]+|[a-z-]+)\s+rows? whose original evidence is permanently lost", re.I),
        "reading": "the ledger scan's permanently-lost rows",
        "value": "ledger_permanent_loss",
    },
    {
        "name": "ledger-owed",
        "pattern": re.compile(r"([0-9]+|[a-z-]+)\s+rows? whose evidence is still owed", re.I),
        "reading": "the ledger scan's still-owed rows",
        "value": "ledger_owed",
    },
    {
        "name": "pin-gap",
        "pattern": re.compile(r"([0-9]+|[a-z-]+)\s+commits? (?:ahead of|behind) (?:the release|the pointer|it)\b", re.I),
        "reading": "the commits between the pinned release and the checkout",
        "value": "pin_gap",
    },
    {
        "name": "pin-gap-live",
        "pattern": re.compile(r"([0-9]+|[a-z-]+)\s+commits? on main are still not live", re.I),
        "reading": "the commits between the pinned release and the checkout",
        "value": "pin_gap",
    },
    {
        "name": "producer-units-naming-release",
        "pattern": re.compile(r"([0-9]+|[a-z][a-z \t-]*?)\s+(?:producer\s+)?units?\s+(?:still\s+)?name[sd]?\s+a release", re.I),
        "reading": "the producer units that name a release directly",
        "value": "producer_units_naming_release",
    },
    {
        # A path in this repository, named together with the commit that
        # carries it.  This is the corpus's own shape for pointing a reader
        # at a source: "`docs/new-senses.md` (commit `35f7cb4`)".  Such a
        # pointer is a claim about the public repository: the reader is told
        # to go and read something there.
        #
        # A bare backticked id is NOT a claim.  The corpus is full of them and
        # they mean different things: a process id ("rows from `41945` and
        # `4176129`"), the head of the retired repository ("`d67bd016`"), and
        # ids a post itself says are no longer reachable from a published
        # branch.  Reading every hex string as a commit made the detector
        # fail on the corpus it is measured against, which is how a detector
        # stops being acted on.
        "name": "source-pointer",
        "pattern": re.compile(
            r"`(?P<path>[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)`[^\n]{0,80}?"
            r"\(commit `(?P<sha>[0-9a-f]{7,40})`\)"),
        "reading": "a source in the public repository",
        "value": "source_pointer",
    },
    {
        # The corpus names the checkout's commit in more than one way.  The
        # correction notice quotes the original wording ("you are on"), and
        # the sentence it corrected now says the checkout "stands at" a
        # commit.  A registry that only knows the first phrasing cannot see
        # the claim the corpus actually makes.
        "name": "checkout-head",
        "pattern": re.compile(r"(?:you are on|stands at)\s*`([0-9a-f]{7,40})`", re.I),
        "reading": "the commit the checkout stands on",
        "value": "checkout_head",
    },
    {
        "name": "field-review-fields",
        "pattern": re.compile(r"grid of ([0-9]+|[a-z-]+) fields\b", re.I),
        "reading": "the fields in the literature grid",
        "value": "field_review_fields",
    },
    {
        "name": "field-review-cells",
        "pattern": re.compile(r"(?:\bof|angles\s*[—–-]|angles,)\s+([0-9]+|[a-z-]+)\s+cells\b", re.I),
        "reading": "the cells in the literature grid",
        "value": "field_review_cells",
    },
    {
        "name": "field-review-landed",
        "pattern": re.compile(r"\b([0-9]+)(?=\s+(?:reviews landed|landed reviews|reviews had landed)\b)", re.I),
        "reading": "the reviews the branch carries",
        "value": "field_review_landed",
    },
    {
        "name": "field-review-pending",
        "pattern": re.compile(r"\b([0-9]+)(?=\s+(?:pending of\s+[0-9]+\s+cells|of\s+[0-9]+\s+cells\s+(?:were|are)\s+pending)\b)", re.I),
        "reading": "the cells with no landed review",
        "value": "field_review_pending",
    },
    # A figure about a tree that has stopped moving.  A post that says "at
    # `d67bd016` the retired repository carries 377 files under
    # `docs/reviews/`" is not describing this plant at all: it names a
    # *frozen* tree in another repository, so its figures cannot drift as
    # the plant moves -- but they can drift when a later author corrects one
    # and gets the tree wrong, and nothing read them.  Each rule below is
    # scoped to a body that names that tree (``RETIRED_SCOPE``) and reads
    # its value from the commit the posts cite.
    #
    # A retired figure is *always* a claim about the frozen tree, whatever
    # its tense: "carried", "named" and "left" describe a tree that has
    # stopped moving, not a past reading of the live plant, so the
    # past-tense guard that turns a stale live figure into history does not
    # apply to these entries.  The scope bound is the guard instead.  Each
    # pattern is written to a wording the corpus actually carries, and the
    # self-test pins those wordings so a pattern that stops matching is
    # caught.
    {
        # "docs/reviews holds 377 entries of which 376 are markdown" and the
        # direct form "377 files under `docs/reviews/`".  The count is of
        # directory entries, not of markdown files: the directory holds one
        # `.json` beside the markdown.
        "name": "retired-reviews-entries",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+(?:entries\b|files?\s+under\s+"
            r"`?docs/reviews/)", re.I),
        "reading": "the entries in the retired repository's docs/reviews",
        "value": "retired_reviews_entries",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # "376 are markdown" and "376 of them markdown".
        "name": "retired-reviews-markdown",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+(?:are\s+markdown|of\s+them\s+markdown)\b",
            re.I),
        "reading": "the markdown reviews in the retired repository",
        "value": "retired_reviews_markdown",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # "245 ... carry the word \"uncommitted\"".  The corpus puts the
        # three numbers of one correction in one sentence ("245 of the 376
        # reviews -- two reviews in three -- carry the word ..."), and names
        # the count as "of them" or "of those" elsewhere, so the rule spans
        # the filler between the number and the verb but binds the number to
        # the word it counts.
        "name": "retired-reviews-uncommitted",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+(?:of\s+(?:the\s+)?[0-9]+\s+)?"
            r"(?:(?:markdown\s+)?reviews?[^\n]{0,45}?|of\s+(?:them|those)\s+)"
            r"carry\w*\s+the\s+words?\s+[\"“]?\s*uncommitted", re.I),
        "reading": "the retired reviews carrying \"uncommitted\"",
        "value": "retired_reviews_uncommitted",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # "140 ... carry the words \"steward lands\"".  The word "also"
        # separates the conjunction ("136 of them also carrying the words
        # \"steward lands\"") from this count, so the rule does not span it.
        "name": "retired-reviews-steward-lands",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+(?:of\s+(?:them|those)\s+)?"
            r"carry\w*\s+(?:the\s+words?\s+)?[\"“]?\s*steward\s+lands", re.I),
        "reading": "the retired reviews carrying \"steward lands\"",
        "value": "retired_reviews_steward_lands",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # The conjunction: "136 carry both", "136 carry both words", and the
        # in-sentence form "136 of them also carrying the words \"steward
        # lands\"" -- there the conjunction is carried by "also" after the
        # uncommitted count, not by the word "both".
        "name": "retired-reviews-both",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+(?:carry\s+both\b|of\s+(?:them|those)\s+"
            r"also\s+carry\w*\s+the\s+words?\s+[\"“]?\s*steward\s+lands)",
            re.I),
        "reading": "the retired reviews carrying both phrases",
        "value": "retired_reviews_both",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # The count by file name, not by content.  The corpus writes it four
        # ways; the filter it names ("in the file name", "names containing")
        # is what distinguishes it from the content count.
        "name": "retired-reviews-deleuze-names",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+(?:of\s+them\s+have\s+names?\s+"
            r"containing\s+`?[\"“]?\s*deleuze|have\s+[\"“]?deleuze[\"”]?\s+"
            r"in\s+the\s+file\s?names?|of\s+the\s+file\s+names?\s+contain\s+"
            r"[\"“]?deleuze|reviews?\s+with\s+`?deleuze`?\s+in\s+the\s+file)",
            re.I),
        "reading": "the retired review names containing \"deleuze\"",
        "value": "retired_reviews_deleuze_names",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # "27 of the 376 review files mention Deleuze or the rhizome" -- the
        # count by content, which the corpus gives beside the name count and
        # a different filter for.
        "name": "retired-reviews-deleuze-content",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+of\s+the\s+[0-9]+\s+review\s+files?\s+"
            r"mention\s+Deleuze", re.I),
        "reading": "the retired reviews whose content mentions Deleuze",
        "value": "retired_reviews_deleuze_content",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # "eight of those begin `deleuze-guattari-`".
        "name": "retired-reviews-deleuze-guattari-names",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+of\s+(?:those|them)\s+(?:begin|carrying)\s+"
            r"`?deleuze-guattari-", re.I),
        "reading": "the retired review names beginning \"deleuze-guattari-\"",
        "value": "retired_reviews_deleuze_guattari_names",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
    {
        # The corpus writes "884 scripts named `mesh-*`".  "named" is a
        # past-tense word, which is exactly why this figure went unread: the
        # pattern binds the number to `mesh-*` and lets the scope be the
        # guard, so the word between them does not matter.
        "name": "retired-mesh-scripts",
        "pattern": re.compile(
            r"([0-9]+|[a-z-]+)\s+scripts?\b[^\n]{0,25}?mesh-\*", re.I),
        "reading": "the mesh-* scripts in the retired repository",
        "value": "retired_mesh_scripts",
        "retired": True,
        "scope": RETIRED_SCOPE,
    },
)
"""The plant readings a published figure can name, and the live value of each."""


def number(text: str) -> int | None:
    """A figure written as a digit or as a word."""
    words = text.strip().lower().replace("-", " ").split()
    if len(words) == 1 and words[0].isdigit():
        return int(words[0])
    # The longest trailing run of words that is a number: "forty one" is
    # forty-one, and "on eleven" (a lazy capture) is eleven.
    for start in range(len(words)):
        number_word = WORDS.get("-".join(words[start:]))
        if number_word is not None:
            return number_word
    return None


def sentences(line: str) -> list[str]:
    """The line's sentences."""
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip()]


def sentence_spans(line: str) -> list[tuple[int, int]]:
    """The line's sentences, as (start, end) offsets."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"(?<=[.!?])\s+", line):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(line)))
    return spans


def clause_spans(text: str, offset: int = 0) -> list[tuple[int, int, str]]:
    """The text's clauses, as (start, end, text) triples.

    A clause boundary is a comma, a semicolon, a colon, or "and".  A figure
    is governed by the clause it sits in, so a qualifier in another clause
    dates only what it points at.  The offsets are into ``text`` shifted by
    ``offset``, so they compare with a figure's offset in the whole line.
    """
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in re.finditer(r"[,;:]|\s+and\s+", text):
        spans.append((start + offset, match.start() + offset, text[start:match.start()]))
        start = match.end()
    spans.append((start + offset, len(text) + offset, text[start:]))
    return [(begin, end, part) for begin, end, part in spans if part.strip()]


def notice_lines(body: str) -> set[int]:
    """The lines that belong to a correction notice."""
    marked: set[int] = set()
    inside = False
    for index, line in enumerate(body.split("\n")):
        if NOTICE.match(line):
            marked.add(index)
            inside = True
            continue
        if inside and line.strip().startswith(">"):
            marked.add(index)
            continue
        inside = False
    return marked


def scope_lines(body: str) -> list[str]:
    """For each line, the text that governs its scope.

    A heading governs its section; the paragraph immediately above a list
    governs that list.  The two are the places a post dates a block of
    figures, and both were found in the live corpus.  Returning the text
    rather than a boolean lets one pass answer both questions about a scope:
    whether it carries a date, and whether it names the retired repository.
    """
    governing: list[str] = []
    heading = ""
    intro: str | None = None
    active_intro: str | None = None
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            governing.append(" ".join(part for part in (heading, active_intro) if part))
            continue
        if stripped.startswith("#"):
            heading = stripped
            intro = None
            active_intro = None
            governing.append(heading)
            continue
        if BULLET.match(line):
            if active_intro is None and intro is not None:
                active_intro = intro
            intro = None
            governing.append(" ".join(part for part in (heading, active_intro) if part))
            continue
        active_intro = None
        intro = line
        governing.append(heading)
    return governing


def figures_in_line(line: str) -> list[tuple[str, str, int, int]]:
    """The registered figures one line states.

    Returns (name, value, start, end) quadruples.  ``value`` is the figure as
    the text writes it; a figure that is neither a digit nor a known number
    word is dropped, because a figure that cannot be read cannot be checked.
    """
    found: list[tuple[str, str, int, int]] = []
    for entry in REGISTRY:
        for match in entry["pattern"].finditer(line):
            if entry["name"] == "source-pointer":
                # The claim is the pair: the path the reader is sent to and
                # the commit they are told to read it at.
                found.append((entry["name"],
                              "%s@%s" % (match.group("path"), match.group("sha")),
                              match.start(), match.end()))
                continue
            # An entry whose wording has two alternatives names the group
            # that matched; every other entry reads the first.
            group = entry.get("group", 1)
            raw = match.group(group)
            if raw is None:
                continue
            if entry["name"] == "checkout-head":
                found.append((entry["name"], raw, match.start(group),
                              match.end(group)))
                continue
            if number(raw) is None:
                continue
            found.append((entry["name"], raw, match.start(group),
                          match.end(group)))
    return found


def is_run_date_reading(text: str, today: date) -> bool:
    """True when the text says it was read again on the run date.

    "Read again on 15 November" or "as read on 15 November" is a claim about
    the plant now only when the date is the run date: any earlier date is a
    dated record of a past reading, and it stays history.  The clause that
    names today's date therefore overrides its own date, and it also keeps
    an earlier clause's date from dating the figures that follow it.
    """
    day = today.day
    month = today.strftime("%B")
    pattern = (r"\b(?:read again on|as read on)\s+(?:%d\s+%s|%s\s+%d)\b"
               % (day, month, month, day))
    return bool(re.search(pattern, text, re.I))



def live_figures(line: str, today: date | None = None,
                 surrounding: str = "") -> list[tuple[str, str, str]]:
    """The registered figures one line states that the text leaves undated.

    Returns (name, value, clause) triples.  A figure is a live claim when
    nothing around it dates it:

    * its own clause carries no date and no past-tense phrase;
    * no clause of its sentence that comes before it carries a date or a
      past-tense phrase -- the qualifier dates the figure it governs, and a
      figure the sentence reports is a reading, not the plant now;
    * the scope around the line carries no date.

    ``surrounding`` is the text that governs the line's scope -- the heading
    above it and the paragraph above a list -- so a retired-repository rule
    can tell whether the line is talking about that tree at all.  A figure
    whose own scope never names the retired repository is not a
    retired-repository claim, however much it looks like one.

    A figure whose own clause says it was read again against the plant now
    is a claim about today even when an earlier clause dates another
    reading on the same sentence.
    """
    found: list[tuple[str, str, str]] = []
    run_day = today or datetime.now(timezone.utc).date()
    for name, raw, start, end in figures_in_line(line):
        entry = next(e for e in REGISTRY if e["name"] == name)
        retired = bool(entry.get("retired"))
        if retired and not RETIRED_SCOPE.search(
                "%s %s" % (surrounding, line)):
            continue
        # A retired figure is a claim about a frozen tree, so a past-tense
        # verb around it ("carried", "named", "left") describes the tree, not
        # a past reading of the live plant.  The scope bound -- the line or
        # its governing text names the tree -- is the guard for these
        # entries, and only a date still marks a retired figure as history.
        for span_start, span_end in sentence_spans(line):
            if not span_start <= start < span_end:
                continue
            offsets = clause_spans(line[span_start:span_end], span_start)
            for index, (part_start, part_end, part) in enumerate(offsets):
                if not part_start <= start < part_end:
                    continue
                if is_run_date_reading(part, run_day):
                    found.append((name, raw, part))
                    break
                if DATE_TOKEN.search(part) or (not retired and PAST.search(part)):
                    break
                if CURRENT.search(part):
                    found.append((name, raw, part))
                    break
                # A clause that names the run date is a live reading, so it
                # does not date the figures after it.
                earlier = " ".join(text for _, _, text in offsets[:index]
                                   if not is_run_date_reading(text, run_day))
                if DATE_TOKEN.search(earlier) or (not retired and PAST.search(earlier)):
                    break
                found.append((name, raw, part))
                break
            break
    return found


def check(bodies: dict[str, str], live: dict[str, object],
          today: date | None = None) -> tuple[list[str], list[str]]:
    """Every live claim the published bodies make, checked against the plant.

    Returns (failures, undecidable).  A failure names the post, the figure it
    states and the live value that contradicts it.  An undecidable entry
    names a figure whose live value could not be read; a check that cannot
    read a reading it needs is UNKNOWN, never a pass.

    ``today`` is the day a "read again on <date>" clause names when it is to
    count as a live claim.  It defaults to the run date; a caller that reads
    a fixed corpus pins it, so a fixture naming a calendar day does not stop
    being a live claim the day after.
    """
    failures: list[str] = []
    undecidable: list[str] = []
    run_day = today or datetime.now(timezone.utc).date()
    for identifier in sorted(bodies, key=lambda value: (len(value), value)):
        body = bodies[identifier] or ""
        marked = notice_lines(body)
        scope = scope_lines(body)
        for index, line in enumerate(body.split("\n")):
            if index in marked:
                continue
            governing = scope[index]
            if governing and DATE_TOKEN.search(governing):
                continue
            for name, raw, clause in live_figures(line, today=run_day,
                                                  surrounding=governing):
                entry = next(e for e in REGISTRY if e["name"] == name)
                value = live.get(entry["value"])
                if entry["value"] == "source_pointer":
                    # A pointer a post gives the reader: a path in the public
                    # repository and the commit to read it at.  The reader
                    # clones the public repository and checks that commit
                    # out, so a commit no published ref reaches is a pointer
                    # that leads nowhere.  A tree the plant cannot read
                    # leaves the claim undecided.
                    published = value if isinstance(value, dict) else None
                    if published is None:
                        undecidable.append(
                            "article %s: the body points at %s, and the published tree could not be read"
                            % (identifier, raw))
                        continue
                    path, _, sha = raw.partition("@")
                    commits = published.get("published_commits") or set()
                    paths = published.get("published_paths")
                    if paths is None:
                        # The remote's head is not in this clone, so whether
                        # the path exists there is undecided.  A missing
                        # answer is never a pass and never a failure.
                        undecidable.append(
                            "article %s: the body points at %s, and this clone does not hold the published head"
                            % (identifier, raw))
                        continue
                    if path.rstrip("/") not in paths:
                        # The path is not in the published repository at all,
                        # so the pointer is wrong about where the source
                        # lives.  This is the retired repository's shape --
                        # its paths are not published here.
                        failures.append(
                            "article %s: the body points at %s, which the public repository does not carry"
                            % (identifier, path))
                        continue
                    if any(str(item).lower().startswith(sha.lower()) for item in commits):
                        continue
                    # The named commit is older than every published head, so
                    # the path is right but the commit is not at the tip.  It
                    # is still a pointer a reader can follow when some
                    # published ref reaches it -- a commit 1761 behind `main`
                    # is exactly that, and failing it would cry wolf on the
                    # corpus's real shape.  Reachability is asked of the
                    # remote, which is the only authority on it.
                    repo = live.get("source_pointer_repo")
                    if repo is None:
                        # A fixture, not the plant: no remote to ask, so the
                        # fixture states reachability itself.
                        reachable = sha.lower() in published.get("reachable", set())
                    else:
                        reachable = reaches(repo, sha)
                    if reachable is None:
                        undecidable.append(
                            "article %s: the body points at %s, and whether a published ref reaches it could not be read"
                            % (identifier, raw))
                        continue
                    if reachable:
                        continue
                    failures.append(
                        "article %s: the body points at commit %s, which no published ref of the public repository reaches"
                        % (identifier, sha))
                    continue
                if entry.get("retired"):
                    # A figure about the retired repository's frozen tree.
                    # A retired tree the plant cannot read leaves the claim
                    # undecided -- never a pass, and never a failure the
                    # check has no evidence for.
                    retired = live.get("retired_tree")
                    if not isinstance(retired, dict):
                        undecidable.append(
                            "article %s: the body says %s %s, and the retired repository could not be read"
                            % (identifier, raw, entry["reading"]))
                        continue
                    measured = retired.get(entry["value"])
                    if not isinstance(measured, int):
                        undecidable.append(
                            "article %s: the body says %s %s, and the retired repository publishes no reading for it"
                            % (identifier, raw, entry["reading"]))
                        continue
                    if number(raw) != measured:
                        failures.append(
                            "article %s: the body says %s %s, the retired repository reads %s"
                            % (identifier, raw, entry["reading"], measured))
                    continue
                if value is None:
                    undecidable.append(
                        "article %s: %s states %s %s, and the plant publishes no live reading for it"
                        % (identifier, entry["reading"], raw, entry["reading"]))
                    continue
                if entry["value"] == "checkout_head":
                    if not str(value).lower().startswith(raw.lower()):
                        failures.append(
                            "article %s: the body says the checkout is on %s, the checkout is on %s"
                            % (identifier, raw, str(value)[:8]))
                    continue
                if number(raw) != value:
                    failures.append(
                        "article %s: the body says %s %s, the plant reads %s"
                        % (identifier, raw, entry["reading"], value))
    return failures, undecidable


def read_key(env_file: str) -> str:
    """The account key, from the site's own credentials file.

    The key never comes from an argument, so it never reaches a process list.
    A credentials file that cannot be read is an error, never an empty key.
    """
    key = os.environ.get("DEVTO_API_KEY", "")
    if not key and env_file:
        try:
            for line in Path(env_file).read_text(encoding="utf-8").splitlines():
                if line.startswith("DEVTO_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
                    break
        except OSError as error:
            raise ValueError("credentials unreadable: %s" % error)
    if not key:
        raise ValueError("no DEVTO_API_KEY in %s" % (env_file or "the environment"))
    return key


def read_account(base: str, key: str) -> list[dict]:
    """Every published article the account holds, with its body."""
    request = urllib.request.Request(
        base + "/api/articles/me/all?per_page=1000",
        headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    if not isinstance(payload, list):
        raise ValueError("the API did not answer with a list of articles")
    published = [article for article in payload if article.get("published")]
    for article in published:
        if not article.get("body_markdown"):
            article["body_markdown"] = read_article(base, key, article["id"]).get("body_markdown") or ""
    return published


def read_article(base: str, key: str, article_id: int) -> dict:
    """One article with its full body."""
    request = urllib.request.Request(
        base + "/api/articles/%d" % article_id,
        headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    if not isinstance(payload, dict):
        raise ValueError("the API did not answer with an article")
    return payload


def git(repo: Path, *args: str) -> tuple[int, str]:
    """Run one git command in the checkout."""
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30,
                              env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin",
                                   "HOME": str(Path.home())})
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, done.stdout.strip() if done.returncode == 0 else (done.stderr.strip() or done.stdout.strip())


def pinned_sha(home: Path) -> str | None:
    """The commit the running release names, or None."""
    link = home / "runtime" / "current"
    if not link.is_symlink():
        return None
    target = link.readlink().name
    return target if re.fullmatch(r"[0-9a-f]{40}", target) else None


def read_ledger(home: Path, max_age_s: float = LEDGER_MAX_AGE_S) -> dict[str, int]:
    """The ledger's own published counters, from the newest discovery sample.

    A reading older than ``max_age_s`` is not a reading of the plant now, so
    it is not used: comparing a published figure against a stale counter
    would report drift where there is only an old sample.  The counter moves
    once a day at most, so an hour is generous.
    """
    directory = home / "discovery"
    if not directory.is_dir():
        return {}
    samples = sorted(directory.glob("scan-*.json"))
    for path in reversed(samples):
        try:
            sample = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for observation in sample.get("observations") or []:
            if not isinstance(observation, dict) or observation.get("id") != SENSE_ID:
                continue
            read_at = observation.get("read_at") or sample.get("created")
            if not isinstance(read_at, str) or not read_at:
                continue
            try:
                stamp = datetime.fromisoformat(read_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            if age < 0 or age > max_age_s:
                continue
            values: dict[str, int] = {}
            for name, key in (("ledger_permanent_loss", "permanent_loss"),
                              ("ledger_owed", "owed_rows")):
                value = observation.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    values[name] = value
            if values:
                return values
    return {}


def read_units_naming_release(unit_dir: Path) -> int | None:
    """How many producer unit files name a release directly."""
    if not unit_dir.is_dir():
        return None
    units = sorted(unit_dir.glob("mishe-producer-*.service"))
    if not units:
        return None
    return sum(1 for unit in units if re.search(r"releases/[0-9a-f]{8,40}", unit.read_text(encoding="utf-8", errors="replace")))


def read_field_review(repo: Path) -> dict[str, object]:
    """The literature grid's live readings, from the lane's own tool and the branch.

    The grid's size comes from fields.json; the covered and total cells come
    from the lane's ``coverage`` command, so this check and the lane cannot
    disagree about what a landed review is.  A reading that cannot be taken
    is left out, which the check reports as UNKNOWN.

    "Reviews landed" is the count of committed ``.md`` files under
    research/field-review/reviews on HEAD (383), which is what the registry
    wording means; the covered-cell count is a different figure and is read
    separately as the pending count.
    """
    live: dict[str, object] = {}
    fields_file = repo / "research" / "field-review" / "fields.json"
    tool = repo / "research" / "field-review" / "field_review.py"
    try:
        grid = json.loads(fields_file.read_text(encoding="utf-8"))
        fields, angles = len(grid["fields"]), len(grid["angles"])
        live["field_review_fields"] = fields
        live["field_review_cells"] = fields * angles
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        done = subprocess.run(
            [sys.executable, "-I", str(tool), "--repo", str(repo), "coverage"],
            capture_output=True, text=True, timeout=120)
        match = re.search(r"covered (\d+) of (\d+) cells", done.stdout)
        if done.returncode == 0 and match:
            covered, total = int(match.group(1)), int(match.group(2))
            live["field_review_cells"] = total
            live["field_review_pending"] = total - covered
    except (OSError, subprocess.SubprocessError):
        pass
    code, out = git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", "research/field-review/reviews")
    if code == 0:
        live["field_review_landed"] = sum(1 for line in out.splitlines() if line.endswith(".md"))
    return live


def read_published_tree(repo: Path) -> dict[str, object] | None:
    """The heads and paths the public repository publishes, or None.

    "Published" means what the *remote* answers, not what this clone's
    ``refs/remotes/origin`` mirror happens to hold.  A pointer a post gives --
    a path in this repository and the commit to read it at -- is followed by
    cloning the public repository and checking that commit out, so a commit no
    published ref reaches is a pointer a reader cannot follow.

    The remote is asked directly because a stale mirror answers confidently
    about a tree that has moved.  On 2026-11-15 the checkout's own
    ``origin/main`` was nine commits behind ``main``; the mirror was internally
    consistent, so a mirror-only reading would have called those commits
    unreachable for the wrong reason and called the remote's real head
    reachable when it was not this clone's.  ``push-lag`` already reports that
    divergence; this check must not depend on the fetch being fresh.

    Paths come from the local ref that matches the remote head, because the
    remote serves commits, not trees.  When the clone does not have the remote
    head, paths are None and a pointer claim is UNKNOWN rather than a pass.
    """
    code, out = git(repo, "ls-remote", "--heads", "origin")
    if code != 0:
        return None
    refs = [line.strip() for line in out.splitlines() if line.strip()]
    if not refs:
        return None
    heads: dict[str, str] = {}
    for line in refs:
        sha, _, name = line.partition("\t")
        if name:
            heads[name.strip()] = sha.strip()
    commits: set[str] = set(heads.values())
    paths: set[str] | None = set()
    for name, sha in heads.items():
        local = "refs/remotes/origin/" + name[len("refs/heads/"):]
        code, resolved = git(repo, "rev-parse", "--verify", "--quiet", local + "^{commit}")
        if code != 0 or resolved.strip() != sha:
            paths = None
            continue
        if paths is None:
            continue
        code, out = git(repo, "ls-tree", "-r", "--name-only", sha)
        if code != 0:
            return None
        paths.update(line.strip().rstrip("/") for line in out.splitlines() if line.strip())
    if not commits:
        return None
    return {"published_commits": commits, "published_paths": paths,
            "published_refs": sorted(heads)}


RETIRED_REPO = Path.home() / "lte-workstation"
"""The retired repository's checkout on this host.

The posts name the retirement commit itself (`d67bd016`), so a reader with
the repository can read the exact tree the post read.  It is a separate
repository from this one and is not the plant's source; it is the source of
the retired fleet's published figures.
"""

RETIRED_COMMIT = "d67bd016231519e618a39537a3a25a247e531b73"
"""The commit that retired the old fleet, as the posts cite it."""


def read_retired_tree(path: Path, commit: str = RETIRED_COMMIT) -> dict[str, int] | None:
    """The retired repository's published readings, or None.

    Read from the commit the posts name rather than from HEAD, because the
    figures are about the tree at the retirement commit: a later commit in
    that repository would answer a question the post never asked.  None when
    the repository is absent or does not hold that commit -- a check that
    cannot read the source of a published figure is UNKNOWN, never a pass.

    The counts are read in one pass each rather than per file: ``git grep``
    answers "which of these paths contain this word" over a whole tree at
    once, and a per-file loop would fork once per review for a number the
    index already holds.
    """
    if not path.is_dir():
        return None
    if git(path, "cat-file", "-e", commit + "^{commit}")[0] != 0:
        return None

    code, out = git(path, "ls-tree", "-r", "--name-only", commit, "--", "docs/reviews")
    if code != 0:
        return None
    names = [line.strip() for line in out.splitlines() if line.strip()]
    if not names:
        return None

    def matching(word: str) -> set[str] | None:
        """The review paths whose text carries ``word``.

        ``git grep -l <treeish> -- <path>`` answers over the whole tree in
        one process; a per-file loop would fork once per review to compute a
        number the index already holds.  None when the tree cannot be
        searched, so the caller reports UNKNOWN rather than comparing a
        published figure against zero.
        """
        code, out = git(path, "grep", "-l", "-i", "-e", word,
                        commit, "--", "docs/reviews")
        if code not in (0, 1):
            return None
        # The output is "<treeish>:<path>", so the path is everything after
        # the first colon.  Splitting on the first field instead collects the
        # commit prefix and answers "one file".
        found: set[str] = set()
        for line in out.splitlines():
            if not line.strip():
                continue
            _, _, name = line.partition(":")
            found.add(name)
        return found

    def carrying(words: tuple[str, ...], require_all: bool) -> int | None:
        """How many review paths satisfy ``words`` under one reading.

        ``require_all`` is the conjunction the corpus states for "136 carry
        both"; without it the words are alternatives, which is how "27 of the
        376 review files mention Deleuze or the rhizome" reads.  The two are
        different questions and must not share a count.
        """
        sets = []
        for word in words:
            found = matching(word)
            if found is None:
                return None
            sets.append(found)
        if not sets:
            return 0
        result = set(sets[0])
        for other in sets[1:]:
            result = result & other if require_all else result | other
        return len(result)

    code, out = git(path, "ls-tree", "-r", "--name-only", commit, "--", "scripts")
    if code != 0:
        return None
    scripts = sum(1 for line in out.splitlines()
                  if line.strip().startswith("scripts/mesh-"))

    entries = {
        "retired_reviews_entries": len(names),
        "retired_reviews_markdown":
            sum(1 for name in names if name.endswith(".md")),
        "retired_reviews_deleuze_names":
            sum(1 for name in names if "deleuze" in name.lower()),
        "retired_reviews_deleuze_guattari_names":
            sum(1 for name in names
                if name.rsplit("/", 1)[-1].startswith("deleuze-guattari-")),
        "retired_mesh_scripts": scripts,
    }
    # Each remaining figure names its own filter, and the corpus says which:
    # 245 of the 376 markdown reviews carry "uncommitted" (one word), 140
    # carry "steward lands" (one word), 136 carry both (the conjunction),
    # and 27 mention Deleuze or the rhizome (the disjunction).
    for key, words, require_all in (
            ("retired_reviews_uncommitted", ("uncommitted",), False),
            ("retired_reviews_steward_lands", ("steward lands",), False),
            ("retired_reviews_both", ("uncommitted", "steward lands"), True),
            ("retired_reviews_deleuze_content", ("deleuze", "rhizome"), False)):
        value = carrying(words, require_all)
        if value is None:
            return None
        entries[key] = value
    return entries

def read_live(home: Path, repo: Path, unit_dir: Path) -> dict[str, object]:
    """Every live value the registry needs, read from the plant."""
    live: dict[str, object] = {}
    live.update(read_ledger(home))
    live.update(read_field_review(repo))

    code, out = git(repo, "rev-parse", "HEAD")
    if code == 0 and out:
        live["checkout_head"] = out

    sha = pinned_sha(home)
    if sha:
        code, out = git(repo, "rev-list", "--count", "%s..HEAD" % sha)
        if code == 0 and out.isdigit():
            live["pin_gap"] = int(out)

    units = read_units_naming_release(unit_dir)
    if units is not None:
        live["producer_units_naming_release"] = units
    published = read_published_tree(repo)
    if published is not None:
        # A pointer may name a commit older than every published head.  Whether
        # a published ref still reaches it is asked of the remote, once, only
        # when a pointer claim actually needs it -- the common case (a commit
        # at the tip) is decided from the heads already read.
        live["source_pointer"] = published
        live["source_pointer_repo"] = repo
    live["retired_tree"] = read_retired_tree(RETIRED_REPO)
    return live


def reaches(repo: Path, sha: str) -> bool | None:
    """Does the public repository serve ``sha`` to a reader?

    Asked of the remote, which is the only authority on it.  The public
    repository serves any commit a ref reaches, so a one-commit fetch is the
    same question a reader who follows the pointer asks, and the answer is
    observed rather than inferred.

    Local ancestry cannot answer it.  This checkout is a shallow clone whose
    floor is ``237db71f`` (``.git/shallow`` names it), so ``merge-base
    --is-ancestor`` answers "no" for every older commit -- and the corpus's
    pointer is exactly such a commit, 1761 behind ``origin/main``.  A
    mirror-only walk was worse still: ``origin/main`` itself stood nine commits
    behind ``main`` on 2026-11-15, so it would have called the checkout's own
    head unreachable for a reason that has nothing to do with the post.

    The fetch writes objects and moves no branch or tag.  It is a no-op when
    the object is already present.  A remote that refuses the commit answers
    False -- that is precisely the 404 a reader gets.  None is reserved for a
    remote that cannot be read at all.
    """
    code, out = git(repo, "ls-remote", "--heads", "origin")
    if code != 0:
        return None
    if not [line for line in out.splitlines() if line.strip()]:
        return None
    # The remote serves object names, not abbreviations, so a short sha the
    # clone knows is expanded first.  One it does not know is already the
    # answer: a reader pasting that abbreviation gets "not our ref" too.
    code, full = git(repo, "rev-parse", "--verify", "--quiet", "%s^{commit}" % sha)
    if code != 0:
        return False
    code, out = git(repo, "fetch", "--quiet", "--no-tags", "--depth", "1", "origin", full)
    if code == 0:
        return True
    text = (out or "").lower()
    if "not our ref" in text or "couldn't find remote ref" in text or "not found" in text:
        return False
    return None


def _self_test() -> int:
    """A controlled failure: a stale plant figure must read RED.

    The fixture is the corpus's real shape -- a figure that names a plant
    reading, stated without a date -- so a claim that drifts is a mutation
    away.
    """
    live = {"ledger_permanent_loss": 126, "ledger_owed": 75, "pin_gap": 3,
            "producer_units_naming_release": 41,
            "checkout_head": "20c65d7309ed4c617913d5317d661912dd23ec56",
            "source_pointer": {
                "published_commits": {"20c65d7309ed4c617913d5317d661912dd23ec56",
                                      "237db71f63d99e2df370418a6b599ed8537efb84"},
                "published_paths": {"docs/new-senses.md"},
                "published_refs": ["refs/remotes/origin/main"],
                # A fixture has no remote to ask, so it states reachability
                # directly: the head itself and one commit behind it.
                "reachable": {"20c65d7309ed4c617913d5317d661912dd23ec56",
                              "237db71f63d99e2df370418a6b599ed8537efb84"}},
            "field_review_fields": 32, "field_review_cells": 192,
            "field_review_landed": 383, "field_review_pending": 0,
            # The retired repository's frozen tree, measured at the commit the
            # posts cite.  A fixture here lets a retired figure be checked
            # without the real retired checkout.
            "retired_tree": {
                "retired_reviews_entries": 377,
                "retired_reviews_markdown": 376,
                "retired_reviews_uncommitted": 245,
                "retired_reviews_steward_lands": 140,
                "retired_reviews_both": 136,
                "retired_reviews_deleuze_names": 19,
                "retired_reviews_deleuze_content": 27,
                "retired_reviews_deleuze_guattari_names": 8,
                "retired_mesh_scripts": 884},
        }
    bodies = {
        "5": "The literature lane is a grid of 32 fields by six angles - 192 cells.\n",
        "6": "`research/field-review`: 383 reviews landed, 0 pending of 192 cells.\n",
        "1": "The current ledger scan finds 126 rows whose original evidence is permanently lost.\n",
        "2": "The repository's current commit is 3 commits ahead of the release the running services are pinned to.\n",
        "3": "Forty-one producer units still name a release directly.\n",
        "4": "The checkout is arborescent (you are on `20c65d7309ed4c617913d5317d661912dd23ec56`).\n",
        # The corpus's own wording: the sentence the 2026-11-15 correction
        # left in place says the checkout "stands at" a commit.
        "7": ("- The checkout is arborescent - it stood at `51a5e68` when this was "
              "written on 29 October, and read again on 15 November it stands at "
              "`20c65d7309ed4c617913d5317d661912dd23ec56`.\n"),
        # The corpus's retired-repository wordings: a figure about the frozen
        # tree the post names.  Each pattern in the retired block is written
        # to one of these, so the fixtures are the pattern's own regression.
        "9": ("At `d67bd016` the retired repository carries 377 files under "
              "`docs/reviews/`. Nineteen of them have names containing "
              "`deleuze`, and eight of those begin `deleuze-guattari-`.\n"),
        "10": ("Against the retired repository: 245 of the 376 reviews carry "
               "the word \"uncommitted\", 140 of them carry the words "
               "\"steward lands\", and 136 carry both, and 884 scripts named "
               "`mesh-*` sit under `scripts/`.\n"),
        "11": ("Against the retired repository, `docs/reviews` holds 376 of "
               "them markdown and 27 of the 376 review files mention Deleuze "
               "or the rhizome.\n"),
    }

    failures, undecidable = check(bodies, live, today=CORPUS_DATE)
    if failures or undecidable:
        print("self-test FAILED: a corpus whose claims hold was read RED:")
        for failure in failures:
            print("  " + failure)
        for entry in undecidable:
            print("  " + entry)
        return 1

    mutations = [
        ("a stale ledger count",
         {"1": "The current ledger scan finds 123 rows whose original evidence is permanently lost.\n"}),
        ("a stale owed count",
         {"1": "The current ledger scan finds 76 rows whose evidence is still owed.\n"}),
        ("a stale pin gap",
         {"2": "The repository's current commit is 990 commits ahead of the release the running services are pinned to.\n"}),
        ("a stale unit count",
         {"3": "Nineteen producer units still name a release directly.\n"}),
        ("a stale checkout commit",
         {"4": "The checkout is arborescent (you are on `51a5e68f6b2103170bfe5fdd415c0ea4c4c923ee`).\n"}),
        ("a stale checkout commit in the corpus's own wording",
         {"7": ("- The checkout is arborescent - it stood at `51a5e68` when this was "
                "written on 29 October, and read again on 15 November it stands at "
                "`f5f7c10b94807b5db97b26d45bf6bad2c295fca0`.\n")}),
        ("a stale field count",
         {"5": "The literature lane is a grid of twelve fields by six angles - 192 cells.\n"}),
        ("a stale cell count",
         {"5": "The literature lane is a grid of 32 fields by six angles - seventy-two cells.\n"}),
        ("a stale landed count",
         {"6": "`research/field-review`: 17 reviews landed, 0 pending of 192 cells.\n"}),
        ("a stale pending count",
         {"6": "`research/field-review`: 383 reviews landed, 55 pending of 192 cells.\n"}),
        # The corpus's own pointer shape: a path in the public repository and
        # the commit to read it at.  A commit no published ref reaches is a
        # pointer a reader cannot follow, and that is the defect the live
        # posts carried until 2026-11-15.
        ("a pointer to a commit no published ref reaches",
         {"8": "The direction is public at `docs/new-senses.md` (commit `35f7cb4`).\n"}),
        ("a pointer to a path the public repository does not carry",
         {"8": "The direction is public at `docs/reviews` (commit `20c65d73`).\n"}),
        # A retired-repository figure gone stale: the frozen tree the post
        # names answers it, so a later author who corrects the figure to the
        # wrong value is caught.  The past-tense wording ("named") that made
        # the mesh-scripts figure unreadable is the fixture's own phrasing.
        ("a stale retired entries count",
         {"9": ("At `d67bd016` the retired repository carries 378 files under "
                "`docs/reviews/`.\n")}),
        ("a stale retired name count",
         {"9": ("At `d67bd016` the retired repository carries 377 files under "
                "`docs/reviews/`; twenty of them have names containing "
                "`deleuze`.\n")}),
        ("a stale retired mesh-scripts count",
         {"10": ("Against the retired repository: 885 scripts named "
                 "`mesh-*` sit under `scripts/`.\n")}),
        ("a stale retired uncommitted count",
         {"10": ("Against the retired repository: 246 of the 376 reviews carry "
                 "the word \"uncommitted\".\n")}),
        ("a stale retired markdown count",
         {"11": ("Against the retired repository, `docs/reviews` holds 375 of "
                 "them markdown.\n")}),
    ]
    for label, mutated in mutations:
        failures, _ = check(mutated, live, today=CORPUS_DATE)
        if not failures:
            print("self-test FAILED: %s was read GREEN" % label)
            return 1

    # "Read again on <run date>" is a claim about the plant now: a stale
    # figure beside it must read RED.  An earlier date is history and stays
    # GREEN, so the claim does not turn into a permanent live claim.
    #
    # The fixtures here name the corpus's own calendar day and are read
    # against it, not against the wall clock: a self-test that reads a fixed
    # date against the day it happens to run breaks on a date, not on a
    # defect.
    today = CORPUS_DATE
    run_day = "%d %s" % (today.day, today.strftime("%B"))
    run_day_true = [
        ("the run-date landed figure, right",
         {"6": "Read again on %s the branch carries 383 landed reviews, 0 of 192 cells are pending.\n" % run_day}),
        ("the run-date grid figure, right",
         {"5": "it is now a grid of 32 fields by six angles — 192 cells as read on %s.\n" % run_day}),
    ]
    for label, mutated in run_day_true:
        failures, undecidable = check(mutated, live, today=today)
        if failures or undecidable:
            print("self-test FAILED: %s was read as a stale claim" % label)
            for failure in failures + undecidable:
                print("  " + failure)
            return 1
    run_day_stale = [
        ("a run-date landed figure gone stale",
         {"6": "Read again on %s the branch carries 17 landed reviews, 0 of 192 cells are pending.\n" % run_day}),
        ("a run-date cell count gone stale, in a later clause",
         {"6": "Read again on %s the branch carries 383 landed reviews, 55 of 192 cells are pending.\n" % run_day}),
        ("a run-date grid figure gone stale",
         {"5": "it is now a grid of twenty-nine fields by six angles — 192 cells as read on %s.\n" % run_day}),
    ]
    for label, mutated in run_day_stale:
        failures, _ = check(mutated, live, today=today)
        if not failures:
            print("self-test FAILED: %s was read GREEN" % label)
            return 1
    old_date = [
        ("a reading dated by an earlier date stays history",
         {"6": "Read again on 11 November the branch carried 17 landed reviews, 0 of 192 cells are pending.\n"}),
    ]
    for label, mutated in old_date:
        failures, _ = check(mutated, live, today=today)
        if failures:
            print("self-test FAILED: %s was read as a live claim" % label)
            return 1

    # The corpus's own sentence is a live claim on the day it names and a
    # dated record on every other day.  Both readings are checked here: a
    # fixture that is a live claim only on the day the check happens to run
    # is a fixture that stops being a live claim tomorrow.
    corpus_sentence = {
        "7": ("- The checkout is arborescent - it stood at `51a5e68` when this was "
              "written on 29 October, and read again on %s it stands at "
              "`f5f7c10b94807b5db97b26d45bf6bad2c295fca0`.\n" % run_day),
    }
    failures, _ = check(corpus_sentence, live, today=today)
    if not failures:
        print("self-test FAILED: a stale commit in the corpus's own wording was read GREEN")
        return 1
    later = today + timedelta(days=14)
    failures, _ = check(corpus_sentence, live, today=later)
    if failures:
        print("self-test FAILED: a reading dated by an earlier date was read as a live claim")
        for failure in failures:
            print("  " + failure)
        return 1

    # A figure the text dates is a record of a reading, not a claim.
    dated = [
        ("a dated ledger figure",
         {"1": "On 11 November, the ledger scan found 123 rows whose original evidence is permanently lost.\n"}),
        ("a dated pin gap",
         {"2": "On 31 October, the repository's commit was 990 commits ahead of the release the running services are pinned to.\n"}),
        ("a figure dated by the paragraph above a list",
         {"3": "Checked against the running system on 11 November.\n\n- Nineteen producer units still name a release directly.\n"}),
        ("a figure dated by the section heading",
         {"3": "## Checked on 11 November\n\n- Nineteen producer units still name a release directly.\n"}),
        ("a figure inside a correction notice",
         {"1": "> **Correction, 2026-11-15.** It said \"the current ledger scan finds 123 rows whose original evidence is permanently lost\".\n"}),
        ("a figure in the past tense",
         {"2": "The gap was 3 commits ahead of the release the running services are pinned to.\n"}),
    ]
    for label, mutated in dated:
        failures, _ = check(mutated, live, today=today)
        if failures:
            print("self-test FAILED: %s was read as a claim" % label)
            for failure in failures:
                print("  " + failure)
            return 1

    # A reading the plant does not publish is UNKNOWN, never a pass.
    failures, undecidable = check({"1": "The current ledger scan finds 123 rows whose original evidence is permanently lost.\n"},
                                   {"ledger_owed": 75})
    if failures or not undecidable:
        print("self-test FAILED: an unreadable reading was not reported as undecidable")
        return 1

    # A retired tree the plant cannot read leaves its figures undecided, and
    # a wrong figure must not be called a pass when the tree is absent.
    failures, undecidable = check(
        {"9": "At `d67bd016` the retired repository carries 378 files under `docs/reviews/`.\n"},
        {})
    if failures or not undecidable:
        print("self-test FAILED: an unreadable retired tree was not reported as undecidable")
        return 1

    print("self-test OK: a corpus whose claims hold reads GREEN and each mutation reads RED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", default="",
                        help="the credentials file carrying DEVTO_API_KEY")
    parser.add_argument("--home", default="",
                        help="the site home, for the plant's own published readings")
    parser.add_argument("--repo", default="",
                        help="the checkout, for the pin and the head")
    parser.add_argument("--unit-dir", default="",
                        help="the systemd user unit directory")
    parser.add_argument("--self-test", action="store_true",
                        help="run the controlled failure and exit")
    parser.add_argument("--json", default="",
                        help="read a capture of the account instead of the live API")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    home = Path(args.home) if args.home else Path(os.environ.get("MISHE_SEED_HOME", "")).resolve()
    repo = Path(args.repo) if args.repo else Path(os.environ.get("MISHE_REPO", "")).resolve()
    unit_dir = Path(args.unit_dir) if args.unit_dir else Path.home() / ".config" / "systemd" / "user"

    try:
        if args.json:
            capture = json.loads(Path(args.json).read_text(encoding="utf-8"))
            published = capture["published"]
            bodies = capture.get("bodies") or {}
        else:
            key = read_key(args.env)
            base = os.environ.get("MESH_DEVTO_API", "https://dev.to")
            published = read_account(base, key)
            if not published:
                raise ValueError("the account reports no published articles")
            bodies = {str(article["id"]): article.get("body_markdown") or ""
                      for article in published}
    except Exception as error:
        print("UNKNOWN: the account could not be read (%s)" % error)
        return 3

    try:
        live = read_live(home, repo, unit_dir)
    except Exception as error:
        print("UNKNOWN: a live reading could not be read (%s)" % error)
        return 3
    failures, undecidable = check(bodies, live)
    for failure in failures:
        print("DRIFT: " + failure)
    for entry in undecidable:
        print("UNDECIDED: " + entry)
    if failures:
        print("VERDICT: %s — a published figure about the plant does not match the plant"
              % RED)
        return 2
    if undecidable:
        print("VERDICT: %s — the plant publishes no live reading for a published figure"
              % UNKNOWN)
        return 3
    print("VERDICT: %s — every published figure about the plant matches the plant"
          % GREEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
