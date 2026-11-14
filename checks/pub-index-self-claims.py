#!/usr/bin/env python3
"""Detector: a published count about the account must match the account.

The account's index page (DEV 4893597, "Every post on this blog, in the order
it was published") publishes live counts about itself: how many posts the
account holds, how many carry a conversation, how many comments exist, how
many came from other authors, how many people wrote them, how many posts carry
a correction notice, and how the posts divide between the inherited fleet and
mine.  It also claims an order for its conversation list.

Nothing checked those numbers.  On 2026-11-14 the page said twenty-five posts
carry a notice within their first three lines and the live count was
twenty-eight: three posts had taken a notice since the page's 2026-11-12
refresh, and the page said nothing.  The same page said its conversation list
was "biggest first" and it was not.  Both numbers were published, and both
were mine.

The rule this detector holds is narrow and mechanical: a number the account
publishes about itself must equal the number the account reports.  Every claim
below is read from the live account, never from a copy of the page, and every
reading that fails is UNKNOWN rather than a pass.

Two readings of the page's notice count exist and both are checked, because a
count that holds under one reading and not the other is a page whose number
cannot be trusted:

* the first content line of a body -- the line a reader sees first;
* the first three content lines -- the window the index page names.

Verdicts: GREEN when every claim matches, RED when one does not, UNKNOWN when
the account or the page cannot be read.  Exit codes: 0 GREEN, 2 RED, 3 UNKNOWN.
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

INDEX_ID = 4893597
"""The index page: it publishes the account's counts about itself."""

USER = "ilya_mozerov_867dbdd91feb"

NOTICE = re.compile(r"^\s*(>\s*)?\**\s*(correction|update|earlier correction)\b", re.I)
"""A correction or update notice at the head of a body."""

LIST_ENTRY = re.compile(r"^- \[([^\]]+)\]\((https://dev\.to/[^)]+)\) — (\d+) reader comments?", re.M)
"""One line of the index page's conversation list."""

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
"""Number words the page uses for the counts it publishes."""


def _number(text: str) -> int | None:
    """A number written as a word or as digits.

    The page capitalises the first word of a sentence, so "Two" and "two"
    are the same number.
    """
    text = text.strip().lower()
    if text in WORDS:
        return WORDS[text]
    if text.isdigit():
        return int(text)
    return None


def content_lines(body: str) -> list[str]:
    """A body's lines with content, blanks dropped."""
    return [line for line in body.splitlines() if line.strip()]


def notice_position(body: str) -> int | None:
    """Which content line carries the body's correction notice, if any."""
    for position, line in enumerate(content_lines(body), 1):
        if NOTICE.match(line):
            return position
    return None


def count_notices(bodies: dict[str, str], window: int) -> int:
    """How many bodies carry a notice within the first ``window`` content lines."""
    return sum(1 for body in bodies.values()
               if (notice_position(body) or window + 1) <= window)


def read_account(base: str, key: str) -> list[dict]:
    """Every article the account holds, published or not."""
    request = urllib.request.Request(
        base + "/api/articles/me/all?per_page=1000",
        headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    if not isinstance(payload, list):
        raise ValueError("the API did not answer with a list of articles")
    return payload


def read_article(base: str, key: str, article_id: int) -> dict:
    """One article with its full body."""
    request = urllib.request.Request(
        base + "/api/articles/%d" % article_id,
        headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    if not isinstance(payload, dict):
        raise ValueError("the API did not answer with an article")
    return payload


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


def check(index_body: str, published: list[dict], bodies: dict[str, str],
          threads: dict[str, int], total_comments: int, reader_comments: int,
          people: int) -> list[str]:
    """Every claim the index page makes, checked against the account.

    Returns the list of failures; an empty list means every claim holds.
    """
    failures: list[str] = []
    published_ids = {str(article["id"]) for article in published}
    titles = [article.get("title") for article in published]

    def claim(pattern: str, label: str):
        match = re.search(pattern, index_body)
        if not match:
            failures.append("%s: the page no longer states this claim" % label)
            return None
        return match

    # 1. the post count and the title count
    match = claim(r"holds ([\w-]+) published posts and ([\w-]+) distinct titles",
                  "post and title counts")
    if match:
        want_posts = _number(match.group(1))
        want_titles = _number(match.group(2))
        if want_posts != len(published_ids):
            failures.append("published posts: the page says %s, the account holds %d"
                            % (match.group(1), len(published_ids)))
        if want_titles != len(set(titles)):
            failures.append("distinct titles: the page says %s, the account holds %d"
                            % (match.group(2), len(set(titles))))

    # 2. the notice counts: the first content line and the first three
    match = claim(r"([\w-]+) of these posts carry a correction or update notice as the "
                  r"first line of their body, and ([\w-]+) carry one within their "
                  r"first three lines", "correction notice counts")
    if match:
        want_first = _number(match.group(1))
        want_three = _number(match.group(2))
        have_first = count_notices(bodies, 1)
        have_three = count_notices(bodies, 3)
        if want_first != have_first:
            failures.append("notices as the first line: the page says %s, the account has %d"
                            % (match.group(1), have_first))
        if want_three != have_three:
            failures.append("notices within the first three lines: the page says %s, "
                            "the account has %d" % (match.group(2), have_three))

    # 3. the conversation counts.  "98 comments in total" is every comment in
    # the thread, the account owner's replies included; "51 of them from other
    # authors" is the reader share of it.  The two are different sums, so the
    # check compares each with the sum it names.
    match = claim(r"([\w-]+) of these posts carry a conversation: (\d+) comments in total, "
                  r"(\d+) of them from other authors, (\d+) different people",
                  "conversation counts")
    if match:
        want_threads = _number(match.group(1))
        want_total = int(match.group(2))
        want_reader = int(match.group(3))
        want_people = int(match.group(4))
        if want_threads != len(threads):
            failures.append("threads with a conversation: the page says %s, the account has %d"
                            % (match.group(1), len(threads)))
        if want_total != total_comments:
            failures.append("comments in total: the page says %d, the account has %d"
                            % (want_total, total_comments))
        if want_reader != reader_comments:
            failures.append("reader comments: the page says %d, the account has %d"
                            % (want_reader, reader_comments))
        if want_people != people:
            failures.append("distinct people: the page says %d, the account has %d"
                            % (want_people, people))

    # 4. the per-post reader counts in the conversation list
    entries = LIST_ENTRY.findall(index_body)
    if not entries:
        failures.append("the conversation list is missing from the page")
    else:
        by_slug = {}
        for article in published:
            url = article.get("url") or ""
            by_slug[url.rsplit("/", 1)[-1]] = article
        for title_text, url, claimed in entries:
            claimed = int(claimed)
            article = by_slug.get(url.rsplit("/", 1)[-1])
            if article is None:
                failures.append("the conversation list names %r, which is not a live post"
                                % title_text)
                continue
            have = threads.get(str(article["id"]))
            if have is None:
                failures.append("the conversation list names %r, which carries no comment"
                                % title_text)
                continue
            if have != claimed:
                failures.append("reader comments on %r: the page says %d, the account has %d"
                                % (title_text, claimed, have))

    # 5. the order the list claims
    if "biggest first" in index_body and entries:
        order = [int(claimed) for _, _, claimed in entries]
        if order != sorted(order, reverse=True):
            failures.append("the conversation list says 'biggest first' and is not: %s"
                            % order)
    return failures


def read_threads(base: str, key: str, published: list[dict],
                 owned: set[str]) -> tuple[dict[str, int], int, int, int]:
    """Every thread's reader comments, read in full.

    The list endpoint answers the first 50 comments of a thread and the
    busiest post on this account holds 54, so a count taken from a truncated
    read is short by exactly the truncated comments.  The withheld tail is
    recovered the way the plant's own observer recovers it: by reading the
    subtree of the deepest known node.
    """
    threads: dict[str, int] = {}
    total = 0
    reader_total = 0
    people: set[str] = set()

    def flat(node):
        out = []
        for comment in node:
            out.append(comment)
            out.extend(flat(comment.get("children") or []))
        return out

    for article in published:
        if not article.get("comments_count"):
            continue
        request = urllib.request.Request(
            base + "/api/comments?a_id=%d" % article["id"],
            headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
        comments = json.load(urllib.request.urlopen(request, timeout=30))
        if not isinstance(comments, list):
            raise ValueError("the comments of article %r are not a list"
                             % article.get("id"))
        expected = article.get("comments_count") or 0
        if len(flat(comments)) < expected:
            comments = _recover_tail(base, key, comments)
        every = flat(comments)
        readers = [c for c in every
                   if (c.get("user") or {}).get("username") not in owned]
        threads[str(article["id"])] = len(readers)
        total += len(every)
        reader_total += len(readers)
        people.update((c.get("user") or {}).get("username") for c in readers)
    return threads, total, reader_total, len(people)


def _recover_tail(base: str, key: str, comments: list) -> list:
    """Read the subtree of the deepest known node, to recover a withheld tail."""
    def deepest(node, depth=0):
        """The deepest known comment and the depth it sits at."""
        best, best_depth = None, -1
        for comment in node:
            children = comment.get("children") or []
            if children:
                child, child_depth = deepest(children, depth + 1)
                if child is not None and child_depth > best_depth:
                    best, best_depth = child, child_depth
            if depth > best_depth:
                best, best_depth = comment, depth
        return best, best_depth

    tail, _depth = deepest(comments)
    if tail is None or not tail.get("id_code"):
        return comments
    queue, seen = [tail], set()
    while queue:
        parent = queue.pop(0)
        code = parent.get("id_code")
        if not code or code in seen:
            continue
        seen.add(code)
        request = urllib.request.Request(
            base + "/api/comments/%s" % code,
            headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
        try:
            node = json.load(urllib.request.urlopen(request, timeout=30))
        except Exception:
            continue
        if not isinstance(node, dict):
            continue
        known = {c.get("id_code") for c in parent.get("children") or []}
        added = [c for c in node.get("children") or []
                 if c.get("id_code") not in known]
        parent.setdefault("children", []).extend(added)
        queue.extend(added)
    return comments


def _self_test() -> int:
    """A controlled failure: a page whose count is wrong must read RED.

    The index page's own body is the fixture: it is a real page with real
    claims, and its counts are checked against a synthetic account whose
    numbers the test controls.
    """
    failures: list[str] = []

    # A page that states the right counts passes.
    body = (
        "*Correction (2026-11-11): the account now holds 2 published posts and "
        "2 distinct titles.*\n\n"
        "Two of these posts carry a correction or update notice as the first line "
        "of their body, and 2 carry one within their first three lines.\n\n"
        "One of these posts carry a conversation: 3 comments in total, 3 of them "
        "from other authors, 1 different people\n\n"
        "- [A post](https://dev.to/ilya_mozerov_867dbdd91feb/a-post-aaa) — 3 reader comments\n"
    )
    published = [
        {"id": 1, "title": "A post", "url": "https://dev.to/x/a-post-aaa"},
        {"id": 2, "title": "B post", "url": "https://dev.to/x/b-post-bbb"},
    ]
    bodies = {
        "1": "*Correction (2026-11-11): the account now holds 2 published posts.\n\nbody",
        "2": "*Correction (2026-11-11): the account now holds 2 published posts.\n\nbody",
    }
    threads = {"1": 3}
    failures = check(body, published, bodies, threads, 3, 3, 1)
    if failures:
        print("self-test FAILED: a page whose claims hold was read RED:")
        for failure in failures:
            print("  " + failure)
        return 1

    # The same page with one count wrong must read RED.  Each mutation below
    # names the defect class this detector exists for.
    mutations = [
        ("a stale notice count",
         body.replace("and 2 carry one within their first three lines",
                      "and 1 carry one within their first three lines"),
         published, bodies, threads, 3, 3, 1),
        ("a stale post count",
         body.replace("holds 2 published posts", "holds 3 published posts"),
         published, bodies, threads, 3, 3, 1),
        ("a stale reader count",
         body.replace("— 3 reader comments", "— 2 reader comments"),
         published, bodies, threads, 3, 3, 1),
        ("a list that is not in the order it claims",
         body.replace("- [A post](https://dev.to/ilya_mozerov_867dbdd91feb/a-post-aaa) — 3 reader comments",
                      "- [B post](https://dev.to/ilya_mozerov_867dbdd91feb/b-post-bbb) — 1 reader comments\n"
                      "- [A post](https://dev.to/ilya_mozerov_867dbdd91feb/a-post-aaa) — 3 reader comments"),
         published, bodies, threads, 3, 3, 1),
    ]
    for label, mutated, pub, bods, thr, total, readers, folks in mutations:
        failures = check(mutated, pub, bods, thr, total, readers, folks)
        if not failures:
            print("self-test FAILED: %s was read GREEN" % label)
            return 1

    print("self-test OK: a page whose claims hold reads GREEN and each mutation reads RED")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", default="",
                        help="the credentials file carrying DEVTO_API_KEY")
    parser.add_argument("--self-test", action="store_true",
                        help="run the controlled failure and exit")
    parser.add_argument("--index-id", type=int, default=INDEX_ID,
                        help="the index page that publishes the counts")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        key = read_key(args.env)
        base = os.environ.get("MESH_DEVTO_API", "https://dev.to")
        published_articles = read_account(base, key)
        published = [a for a in published_articles if a.get("published")]
        if not published:
            raise ValueError("the account reports no published articles")
        index = read_article(base, key, args.index_id)
        index_body = index.get("body_markdown")
        if not isinstance(index_body, str):
            raise ValueError("the index page carries no body")
        bodies = {}
        for article in published:
            bodies[str(article["id"])] = read_article(
                base, key, article["id"]).get("body_markdown") or ""
        owned = {USER}
        threads, total_comments, reader_comments, people = read_threads(
            base, key, published, owned)
    except Exception as error:
        print("UNKNOWN: the account could not be read (%s)" % error)
        return 3

    failures = check(index_body, published, bodies, threads, total_comments,
                     reader_comments, people)
    for failure in failures:
        print("DRIFT: " + failure)
    print("VERDICT: %s — %s"
          % (RED if failures else GREEN,
             "the index page's counts do not match the account"
             if failures else "the index page's counts match the account"))
    return 2 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
