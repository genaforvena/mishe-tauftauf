#!/usr/bin/env python3
"""Detector: a count the account publishes about itself must be the account's count.

The gap this closes.  On 2026-11-14 the live article 4894878 ("How long each
of these stayed broken before anyone noticed") published two counts about the
account.  Its body said "This account has published 78 posts" and its card
description said "This account has published 69 posts"; the account held 95.
The page's own correction trail at its head had already reached 95 a day
earlier, so the page contradicted itself and nothing said so.

Why the checks that existed did not see it:

* ``checks/pub-index-self-claims.py`` reads one page, the index (4893597).
* ``pub/bin/devto-count-parity`` reads the FIRST "account now holds N
  published posts" sentence of each count page.  On 4894878 that sentence
  lives inside a correction notice and reads 95, so parity passed while the
  prose sentence a reader actually reads said 78.

Both read a count page's correction head.  Neither reads the sentence in the
body that tells the reader what the account holds.  This detector reads every
published body and every card description, so a false count is caught where it
is written, not only where the correction trail happens to sit.

The rule.  A line qualifies when it names the account (account, blog, index,
page, list, publication) and states a total for it -- "this account has
published N posts", "the published blog this account maintains is N posts, M
distinct titles".  The number must equal the account's live published-post
count, and the distinct-title count must equal the account's live count of
distinct titles.

Two things are deliberately NOT claims, because a detector that flags them
would cry wolf on the corpus it is measured against:

* a correction notice line.  A correction trail is a record of what the
  account held on past dates; those numbers are history and are meant to
  disagree with today.
* a figure the line dates.  "at the time this draft was written", "then
  held", "before that", "as of the 2026-11-12 refresh" place a figure in a
  past time, and the account's count today is not what it claims to be.

A qualifier dates the figure it qualifies, not the whole line.  The figure's
own sentence governs it, and another sentence on the line governs it only
when it points back at the figure ("the count above", "both figures", "the
account size").  This scope is measured, not guessed: the earlier whole-line
rule skipped a live claim on article 4949922, whose bullet carries the
figure and the note "The count above was read at dashboard time on
2026-10-30 and read again against the live account on 2026-11-14" on one
line, and mutating that figure to a stale one still read GREEN.  A figure
whose governing sentence says it was read against the live account is a
claim about today even when the same sentence also dates an earlier read.

The threshold is measured, not guessed: over the 95 live bodies captured on
2026-11-14 this rule reads exactly three figures as claims, all of them
true, and leaves the dated ones alone.  Every qualifier phrase it knows was
taken from a live line.

Verdicts: GREEN when every claim matches, RED when one does not, UNKNOWN when
the account cannot be read.  Exit codes: 0 GREEN, 2 RED, 3 UNKNOWN.  A
detector that cannot read its input answers UNKNOWN, never a pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"

NOTICE = re.compile(r"^\s*(>\s*)?\**\s*(correction|update|earlier correction|superseded)\b", re.I)
"""A correction or update notice.  Its numbers are history, not a claim."""

DATED = re.compile(
    r"at the time|at dashboard time|first read at|then (?:held|read|had)|"
    r"historically|before that|before an earlier|earlier correction|"
    r"was written|had published|now reads|count(?:ed)? now|had been|"
    r"has since been published|as of the \d{4}-\d{2}-\d{2} refresh|"
    r"refresh was read|was read again|in the \d{4}-\d{2}-\d{2} refresh|"
    r"earlier refresh|as they stood", re.I)
"""A phrase that places a figure in a past time.

A dated figure is a record of what the account held then, not a claim about
what it holds now.  The phrase dates the figure it belongs to, not every
figure on the line.
"""

PROVENANCE = re.compile(
    r"was read at|read at dashboard time|first read at|"
    r"refresh was read|was read again|was read against|was re-read|"
    r"verified against", re.I)
"""A phrase that says when a figure was obtained.

Reading a number on a given day does not make the number a claim about
today: it describes the account as it was on that day, unless the same
sentence says it was read against the account as it is now.  A figure that
carries a provenance phrase and no fresh read is dated by it.
"""

POINTER = re.compile(
    r"the count (?:below|above|then|now)|"
    r"(?:the|these|those|both|that|either)\s+"
    r"(?:counts?|figures?|account size|account counts)\b", re.I)
"""A phrase that points at a figure elsewhere on the line.

"The count above was read at dashboard time" says when THAT figure was read;
it says nothing about a different figure on the same line.
"""

CURRENT = re.compile(
    r"against the live account|against the account|"
    r"verified against a live list|fresh read of /articles/me/all|"
    r"re-read against", re.I)
"""A phrase that says the figure was read against the account as it is now.

A page that says so has re-verified the figure, so the figure is a claim
about today even when the same sentence also dates an earlier read.
"""

ACCOUNT = re.compile(r"\b(?:account|blog|index|page|list|publication)\b", re.I)
"""The subject of the claim.  A line that names none of these is not about
the account's own size."""

# "this account has published 78 posts", "the blog holds 95 published posts"
TOTAL = re.compile(
    r"\b(?:this|the|our|my)?\s*(?:account|blog|index|page|list|publication)\b"
    r"[^.;:]{0,40}?\b(?:has published|have published|holds|hold|maintains|"
    r"maintain|carries|carry)\b[^.;:]{0,20}?(\d+)\s+(?:published\s+)?posts\b",
    re.I)
"""A total of posts the account holds."""

# "is 62 posts, 58 distinct titles" -- the pair, with the emphasis markers
# a writer puts around the numbers.
PAIR = re.compile(
    r"\b(?:account|blog|index|page|list|publication)\b"
    r"[^.;:]{0,60}?\b(\d+)\s+(?:published\s+)?posts\b"
    r"[^.;:]{0,20}?(\d+)\s+distinct titles\b",
    re.I)
"""A total of posts together with the number of distinct titles."""

TITLES = re.compile(r"\b(\d+)\s+distinct titles\b", re.I)
"""A count of distinct titles on its own."""


def content_lines(body: str) -> list[str]:
    """A body's lines with content, blanks dropped."""
    return [line for line in body.splitlines() if line.strip()]


SENTENCE = re.compile(r"(?<=[.!?;])\s+")
"""A sentence boundary inside one line of prose."""


def sentence_spans(line: str) -> list[tuple[int, int]]:
    """The line's sentences, as (start, end) offsets."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in SENTENCE.finditer(line):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(line)))
    return spans


def figures_in_line(line: str) -> list[tuple[str, int, int | None, list[int]]]:
    """The account figures one line states.

    Returns (kind, posts, titles, positions) quadruples.  ``titles`` is None
    when the line states no distinct-title count.  ``positions`` are the
    offsets of the figures inside the line, so a rule can tell which sentence
    holds which figure.
    """
    pair = PAIR.search(line)
    if pair:
        return [("total with titles", int(pair.group(1)), int(pair.group(2)),
                 [pair.start(1), pair.start(2)])]
    total = TOTAL.search(line)
    if total:
        titles = TITLES.search(line)
        positions = [total.start(1)]
        if titles:
            positions.append(titles.start(1))
        return [("total", int(total.group(1)),
                 int(titles.group(1)) if titles else None, positions)]
    return []


def claims_in_line(line: str) -> list[tuple[str, int, int | None]]:
    """The account claims one line states.

    Returns a list of (kind, posts, titles) triples.  ``titles`` is None when
    the line states no distinct-title count.

    A qualifier dates the figure it qualifies, not the whole line.  The
    figure's own sentence governs it; a sentence elsewhere on the line governs
    it only when it points back at the figure ("the count above", "both
    figures", "the account size").  A figure the governing sentences say was
    read against the account as it is now is a claim about today even when
    the same sentence also dates an earlier read of it.

    This matters because the whole-line rule missed a live claim: on
    2026-11-14 the bullet on article 4949922 read "the published blog this
    account maintains is 95 posts, 91 distinct titles.  The count above was
    read at dashboard time on 2026-10-30 and read again against the live
    account on 2026-11-14", and mutating that figure to a stale one still
    read GREEN, because the dated note swallowed the whole line.
    """
    if NOTICE.match(line):
        return []
    if not ACCOUNT.search(line):
        return []
    spans = sentence_spans(line)
    found: list[tuple[str, int, int | None]] = []
    for kind, posts, titles, positions in figures_in_line(line):
        own = [span for span in spans
               if any(span[0] <= position < span[1] for position in positions)]
        governing = [line[start:end] for start, end in own]
        for start, end in spans:
            if any((start, end) == span for span in own):
                continue
            text = line[start:end]
            if POINTER.search(text):
                governing.append(text)
        if any(CURRENT.search(text) for text in governing):
            found.append((kind, posts, titles))
            continue
        if any(DATED.search(text) for text in governing):
            continue
        if any(PROVENANCE.search(text) for text in governing):
            continue
        found.append((kind, posts, titles))
    return found


def claims_in_body(body: str) -> list[tuple[str, int, int | None]]:
    """Every account claim a body states."""
    found: list[tuple[str, int, int | None]] = []
    for line in content_lines(body):
        found.extend(claims_in_line(line))
    return found


def check(published: list[dict], bodies: dict[str, str],
          descriptions: dict[str, str]) -> list[str]:
    """Every claim the published pages make about the account, checked.

    Returns the list of failures; an empty list means every claim holds.

    Every body the caller handed over is read, not only the bodies of the
    articles it listed as published: a body that carries a claim and is
    missing from the list would otherwise be skipped, and a claim that was
    read but not checked is the same false negative as a claim never read.
    """
    failures: list[str] = []
    live_posts = len(published)
    live_titles = len({article.get("title") for article in published})
    titles_by_id = {str(article["id"]): article.get("title") or ""
                    for article in published}
    identifiers = sorted(set(titles_by_id) | set(bodies) | set(descriptions),
                          key=lambda value: (len(value), value))
    for identifier in identifiers:
        title = titles_by_id.get(identifier, "")
        sources = [("body", bodies.get(identifier) or "")]
        if identifier in descriptions:
            sources.append(("card description", descriptions[identifier]))
        for where, text in sources:
            for kind, posts, titles in claims_in_body(text):
                if posts != live_posts:
                    failures.append(
                        "article %s (%s): %s says the account holds %d published posts, "
                        "the account holds %d" % (identifier, title, where, posts, live_posts))
                if titles is not None and titles != live_titles:
                    failures.append(
                        "article %s (%s): %s says the account holds %d distinct titles, "
                        "the account holds %d" % (identifier, title, where, titles, live_titles))
    return failures


def read_key(env_file: str) -> str:
    """The account key, from the site's own credentials file.

    The key never comes from an argument, so it never reaches a process list.
    A credentials file that cannot be read is an error, never an empty key:
    reporting "no drift" from an account that could not be read would be the
    false negative this detector exists to avoid.
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
    """Every published article the account holds."""
    request = urllib.request.Request(
        base + "/api/articles/me/all?per_page=1000",
        headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    if not isinstance(payload, list):
        raise ValueError("the API did not answer with a list of articles")
    return [article for article in payload if article.get("published")]


def read_article(base: str, key: str, article_id: int) -> dict:
    """One article with its full body and card description."""
    request = urllib.request.Request(
        base + "/api/articles/%d" % article_id,
        headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    if not isinstance(payload, dict):
        raise ValueError("the API did not answer with an article")
    return payload


def _self_test() -> int:
    """A controlled failure: a page whose count is wrong must read RED.

    The fixture is the account's real published shape -- two posts, one of
    them twice -- so the numbers the pages state are the ones the fixture
    account reports, and a claim that drifts is a mutation away.
    """
    failures: list[str] = []

    published = [
        {"id": 1, "title": "A post", "url": "https://dev.to/x/a-post-aaa"},
        {"id": 2, "title": "A post", "url": "https://dev.to/x/a-post-aaa"},
        {"id": 3, "title": "B post", "url": "https://dev.to/x/b-post-bbb"},
    ]
    bodies = {
        "1": "This account has published 3 posts, this one included.\n",
        "2": "This account has published 3 posts, this one included.\n",
        "3": "This account has published 3 posts, this one included.\n",
    }
    descriptions = {"1": "This account has published 3 posts, this one included."}

    # A corpus whose claims hold passes.
    failures = check(published, bodies, descriptions)
    if failures:
        print("self-test FAILED: a corpus whose claims hold was read RED:")
        for failure in failures:
            print("  " + failure)
        return 1

    # The same corpus with one count wrong must read RED.  Each mutation
    # names the defect class this detector exists for.
    mutations = [
        ("a stale account total in a body",
         dict(bodies, **{"3": "This account has published 2 posts, this one included.\n"}),
         descriptions),
        ("a stale account total in a card description",
         bodies,
         {"1": "This account has published 2 posts, this one included."}),
        ("a stale distinct-title count",
         dict(bodies, **{"3": "The published blog this account maintains is **3 posts, "
                               "3 distinct titles** (four duplicate pairs).\n"}),
         descriptions),
        ("a count the line itself dates is not a claim",
         dict(bodies, **{"3": "The count-correction history of this blog -- this account "
                               "has published **2 posts** at the time this draft was "
                               "written -- is itself the ledger.\n"}),
         descriptions),
        # The wake-22 gap: a live figure and a dated note on one line.  The
        # note dates the figure it points at, so it must not swallow a
        # different figure that the same line states and re-verifies.
        ("a stale count beside a dated note on the same line",
         dict(bodies, **{"3": "The published blog this account maintains is **2 posts, "
                               "2 distinct titles**.  The count above was read at "
                               "dashboard time on 2026-10-30 and read again against the "
                               "live account on 2026-11-14.\n"}),
         descriptions),
    ]
    for label, mutated, descs in mutations:
        failures = check(published, mutated, descs)
        if label.startswith("a count the line itself dates"):
            if failures:
                print("self-test FAILED: %s was read as a claim" % label)
                return 1
            continue
        if not failures:
            print("self-test FAILED: %s was read GREEN" % label)
            return 1

    # A claim about another account's size is not a claim about this one.
    failures = check(published,
                     dict(bodies, **{"3": "The retired fleet had 40 posts, this one "
                                           "included.\n"}),
                     descriptions)
    if failures:
        print("self-test FAILED: a claim about another fleet was read as a claim "
              "about this account")
        return 1

    print("self-test OK: a corpus whose claims hold reads GREEN and each mutation reads RED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", default="",
                        help="the credentials file carrying DEVTO_API_KEY")
    parser.add_argument("--self-test", action="store_true",
                        help="run the controlled failure and exit")
    parser.add_argument("--json", default="",
                        help="read a capture of the account instead of the live API")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        if args.json:
            capture = json.loads(Path(args.json).read_text(encoding="utf-8"))
            published = capture["published"]
            bodies = capture.get("bodies") or {}
            descriptions = capture.get("descriptions") or {}
        else:
            key = read_key(args.env)
            base = os.environ.get("MESH_DEVTO_API", "https://dev.to")
            published = read_account(base, key)
            if not published:
                raise ValueError("the account reports no published articles")
            bodies = {}
            descriptions = {}
            for article in published:
                one = read_article(base, key, article["id"])
                bodies[str(article["id"])] = one.get("body_markdown") or ""
                description = one.get("description")
                if isinstance(description, str) and description.strip():
                    descriptions[str(article["id"])] = description
    except Exception as error:
        print("UNKNOWN: the account could not be read (%s)" % error)
        return 3

    failures = check(published, bodies, descriptions)
    for failure in failures:
        print("DRIFT: " + failure)
    print("VERDICT: %s — %s"
          % (RED if failures else GREEN,
             "a published count about the account does not match the account"
             if failures else
             "every published count about the account matches the account"))
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
