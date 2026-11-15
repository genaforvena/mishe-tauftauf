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
  November" -- these report a reading, not the plant's state.

Scope is measured per clause, not per line and not per body.  A qualifier
dates the figure it qualifies: the figure's own clause governs it, the first
clause of its sentence governs the sentence when it carries a date, the
paragraph immediately above a list governs that list, and the heading governs
its section.  A figure whose own clause is in the past tense ("the gap was 3
commits", "the scan found 126 rows") is a record of a reading, so it is not a
claim about the plant now.

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
from datetime import date, datetime, timezone
from pathlib import Path

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"

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
        "pattern": re.compile(r"([0-9]+|[a-z-]+)\s+(?:producer\s+)?units?\s+(?:still\s+)?name[sd]?\s+a release", re.I),
        "reading": "the producer units that name a release directly",
        "value": "producer_units_naming_release",
    },
    {
        "name": "checkout-head",
        "pattern": re.compile(r"you are on `([0-9a-f]{7,40})`", re.I),
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
)
"""The plant readings a published figure can name, and the live value of each."""


def number(text: str) -> int | None:
    """A figure written as a digit or as a word."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    return WORDS.get(text)


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


def scope_dates(body: str) -> list[bool]:
    """For each line, whether the scope around it carries a date.

    A heading governs its section; the paragraph immediately above a list
    governs that list.  The two are the places a post dates a block of
    figures, and both were found in the live corpus.
    """
    dated: list[bool] = []
    heading_dated = False
    intro: str | None = None
    active_intro: str | None = None
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            dated.append(heading_dated or bool(active_intro and DATE_TOKEN.search(active_intro)))
            continue
        if stripped.startswith("#"):
            heading_dated = bool(DATE_TOKEN.search(stripped))
            intro = None
            active_intro = None
            dated.append(heading_dated)
            continue
        if BULLET.match(line):
            if active_intro is None and intro is not None:
                active_intro = intro
            intro = None
            dated.append(heading_dated or bool(active_intro and DATE_TOKEN.search(active_intro)))
            continue
        active_intro = None
        intro = line
        dated.append(heading_dated)
    return dated


def figures_in_line(line: str) -> list[tuple[str, str, int, int]]:
    """The registered figures one line states.

    Returns (name, value, start, end) quadruples.  ``value`` is the figure as
    the text writes it; a figure that is neither a digit nor a known number
    word is dropped, because a figure that cannot be read cannot be checked.
    """
    found: list[tuple[str, str, int, int]] = []
    for entry in REGISTRY:
        for match in entry["pattern"].finditer(line):
            raw = match.group(1)
            if entry["name"] == "checkout-head":
                found.append((entry["name"], raw, match.start(1), match.end(1)))
                continue
            if number(raw) is None:
                continue
            found.append((entry["name"], raw, match.start(1), match.end(1)))
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


def live_figures(line: str, today: date | None = None) -> list[tuple[str, str, str]]:
    """The registered figures one line states that the text leaves undated.

    Returns (name, value, clause) triples.  A figure is a live claim when
    nothing around it dates it:

    * its own clause carries no date and no past-tense phrase;
    * no clause of its sentence that comes before it carries a date or a
      past-tense phrase -- the qualifier dates the figure it governs, and a
      figure the sentence reports is a reading, not the plant now;
    * the scope around the line carries no date.

    A figure whose own clause says it was read again against the plant now
    is a claim about today even when an earlier clause dates another
    reading on the same sentence.
    """
    found: list[tuple[str, str, str]] = []
    run_day = today or datetime.now(timezone.utc).date()
    for name, raw, start, end in figures_in_line(line):
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
                if DATE_TOKEN.search(part) or PAST.search(part):
                    break
                if CURRENT.search(part):
                    found.append((name, raw, part))
                    break
                # A clause that names the run date is a live reading, so it
                # does not date the figures after it.
                earlier = " ".join(text for _, _, text in offsets[:index]
                                   if not is_run_date_reading(text, run_day))
                if DATE_TOKEN.search(earlier) or PAST.search(earlier):
                    break
                found.append((name, raw, part))
                break
            break
    return found


def check(bodies: dict[str, str], live: dict[str, object]) -> tuple[list[str], list[str]]:
    """Every live claim the published bodies make, checked against the plant.

    Returns (failures, undecidable).  A failure names the post, the figure it
    states and the live value that contradicts it.  An undecidable entry
    names a figure whose live value could not be read; a check that cannot
    read a reading it needs is UNKNOWN, never a pass.
    """
    failures: list[str] = []
    undecidable: list[str] = []
    for identifier in sorted(bodies, key=lambda value: (len(value), value)):
        body = bodies[identifier] or ""
        marked = notice_lines(body)
        scoped = scope_dates(body)
        for index, line in enumerate(body.split("\n")):
            if index in marked:
                continue
            if scoped[index]:
                continue
            for name, raw, clause in live_figures(line):
                entry = next(e for e in REGISTRY if e["name"] == name)
                value = live.get(entry["value"])
                if value is None:
                    undecidable.append(
                        "article %s: %s states %s %s, and the plant publishes no live reading for it"
                        % (identifier, entry["reading"], raw, entry["reading"]))
                    continue
                if entry["value"] == "checkout_head":
                    if not str(value).startswith(raw):
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
    return live


def _self_test() -> int:
    """A controlled failure: a stale plant figure must read RED.

    The fixture is the corpus's real shape -- a figure that names a plant
    reading, stated without a date -- so a claim that drifts is a mutation
    away.
    """
    live = {"ledger_permanent_loss": 126, "ledger_owed": 75, "pin_gap": 3,
            "producer_units_naming_release": 41,
            "checkout_head": "20c65d7309ed4c617913d5317d661912dd23ec56",
            "field_review_fields": 32, "field_review_cells": 192,
            "field_review_landed": 383, "field_review_pending": 0}
    bodies = {
        "5": "The literature lane is a grid of 32 fields by six angles - 192 cells.\n",
        "6": "`research/field-review`: 383 reviews landed, 0 pending of 192 cells.\n",
        "1": "The current ledger scan finds 126 rows whose original evidence is permanently lost.\n",
        "2": "The repository's current commit is 3 commits ahead of the release the running services are pinned to.\n",
        "3": "Forty-one producer units still name a release directly.\n",
        "4": "The checkout is arborescent (you are on `20c65d7309ed4c617913d5317d661912dd23ec56`).\n",
    }

    failures, undecidable = check(bodies, live)
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
        ("a stale field count",
         {"5": "The literature lane is a grid of twelve fields by six angles - 192 cells.\n"}),
        ("a stale cell count",
         {"5": "The literature lane is a grid of 32 fields by six angles - seventy-two cells.\n"}),
        ("a stale landed count",
         {"6": "`research/field-review`: 17 reviews landed, 0 pending of 192 cells.\n"}),
        ("a stale pending count",
         {"6": "`research/field-review`: 383 reviews landed, 55 pending of 192 cells.\n"}),
    ]
    for label, mutated in mutations:
        failures, _ = check(mutated, live)
        if not failures:
            print("self-test FAILED: %s was read GREEN" % label)
            return 1

    # "Read again on <run date>" is a claim about the plant now: a stale
    # figure beside it must read RED.  An earlier date is history and stays
    # GREEN, so the claim does not turn into a permanent live claim.
    today = datetime.now(timezone.utc).date()
    run_day = "%d %s" % (today.day, today.strftime("%B"))
    run_day_true = [
        ("the run-date landed figure, right",
         {"6": "Read again on %s the branch carries 383 landed reviews, 0 of 192 cells are pending.\n" % run_day}),
        ("the run-date grid figure, right",
         {"5": "it is now a grid of 32 fields by six angles — 192 cells as read on %s.\n" % run_day}),
    ]
    for label, mutated in run_day_true:
        failures, undecidable = check(mutated, live)
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
        failures, _ = check(mutated, live)
        if not failures:
            print("self-test FAILED: %s was read GREEN" % label)
            return 1
    old_date = [
        ("a reading dated by an earlier date stays history",
         {"6": "Read again on 11 November the branch carried 17 landed reviews, 0 of 192 cells are pending.\n"}),
    ]
    for label, mutated in old_date:
        failures, _ = check(mutated, live)
        if failures:
            print("self-test FAILED: %s was read as a live claim" % label)
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
        failures, _ = check(mutated, live)
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

    live = read_live(home, repo, unit_dir)
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
