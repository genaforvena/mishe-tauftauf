#!/usr/bin/env python3
"""Detector: an article body must not repeat a paragraph verbatim.

The gap this closes.  On 2026-11-14 the live article 4954898 (Guattari, *The
Three Ecologies*) carried the same closing paragraph twice, in two different
sections: the rewrite that night rebuilt the three-lens section and re-emitted
the paragraph that already closed the earlier section.  A reader read the same
sentence twice.  The wake's own check compared quotations and missed it, and
nothing in the write path compared the body with itself -- the publisher's
read-back compares the bytes it sent with the bytes the server stored, so a
body that repeats itself matches perfectly.

The rule.  Split the body into paragraphs on blank lines, drop fenced code
blocks (a repeated snippet inside a code fence is not a repeated paragraph),
normalise the whitespace of what is left, and report every paragraph of at
least ``MIN_PARAGRAPH_CHARS`` characters that occurs more than once.  The
threshold is measured, not guessed: over the 95 live bodies captured on
2026-11-14 the only repeated paragraph of 120 characters or more is the defect
itself, while the longest deliberate refrain in that corpus (article 4894878
repeats a 60-character refrain nine times on purpose, as part of the piece)
stays under it.  A rule that cried wolf on the corpus it is measured against
would be no rule at all.

Three modes, because there are three questions:

* ``--body FILE`` — the body about to be written.  This is the publication
  pre-flight: the write path refuses on a finding.
* ``--bodies FILE`` — a capture of many bodies (a JSON map of article id to
  body).  This is the standing observation over the live account.
* ``--stdin`` — the body on standard input, which is what the write path has
  (it holds the body in memory, not in a file).
* ``--live`` — every published body on the DEV account, read through the
  account's own API.  This is the standing observation: the write pre-flight
  stops the next repetition, but only a standing read can say that no live
  article repeats a paragraph today.
* ``--self-test`` — a controlled failure: a body that repeats a paragraph must
  read RED and a body that does not must read GREEN.  A detector that cannot
  fail on a known repetition cannot be trusted to fail on one.

Exit status: 0 no repeated paragraph, 2 one or more found, 3 the question
could not be decided (an unreadable file, a malformed capture).  A detector
that cannot read its input answers UNKNOWN, never a pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

MIN_PARAGRAPH_CHARS = 120
"""The shortest paragraph that counts as prose worth repeating.

Measured against the live corpus on 2026-11-14: the defective paragraph is
191 characters and is the only repeat at or above 120, while the corpus's
longest deliberate refrain is 60 characters and repeats nine times.
"""

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"

_FENCE = re.compile(r"^```.*?^```", re.S | re.M)
"""A fenced code block, including the fence lines themselves."""


def _strip_code(body: str) -> str:
    """The body without its fenced code blocks."""
    return _FENCE.sub("", body)


def _normalise(paragraph: str) -> str:
    """One paragraph's text, with every run of whitespace collapsed."""
    return " ".join(paragraph.split())


def paragraphs(body: str) -> list[str]:
    """The body split into paragraphs, code fences removed, blanks dropped."""
    chunks = re.split(r"\n\s*\n", _strip_code(body))
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def find_duplicates(body: str, threshold: int = MIN_PARAGRAPH_CHARS) -> list[dict]:
    """Every paragraph that occurs more than once, at or above the threshold.

    Each finding carries the repeated text, how many times it occurs, and the
    0-based paragraph positions, so a reader of the report can find it in the
    body rather than take the word for it.
    """
    seen: dict[str, list[int]] = {}
    for index, chunk in enumerate(paragraphs(body)):
        text = _normalise(chunk)
        if len(text) < threshold:
            continue
        seen.setdefault(text, []).append(index)
    findings = []
    for text, positions in seen.items():
        if len(positions) > 1:
            findings.append({"text": text, "count": len(positions),
                             "positions": positions})
    findings.sort(key=lambda finding: (-finding["count"], finding["positions"][0]))
    return findings


def _bodies_from_capture(data: object) -> list[tuple[str, str]]:
    """The (id, body) pairs in a capture of many bodies.

    A capture maps an article id to its body, either as the body text itself
    or as an object carrying a ``body`` field.  Both shapes are accepted
    because the two captures the plant keeps differ: one holds the body
    directly, the other holds the whole article record.
    """
    if not isinstance(data, dict):
        raise ValueError("capture is not a map of article id to body")
    pairs = []
    for key, value in data.items():
        if isinstance(value, str):
            pairs.append((str(key), value))
        elif isinstance(value, dict) and isinstance(value.get("body"), str):
            pairs.append((str(key), value["body"]))
        else:
            raise ValueError("capture entry %r carries no body" % (key,))
    return pairs


def _report(findings: list[dict], label: str) -> None:
    """Print the findings; the last line is the verdict the runner reads."""
    for finding in findings:
        print("REPEATED PARAGRAPH in %s: %d times at paragraphs %s"
              % (label, finding["count"],
                 ", ".join(str(position) for position in finding["positions"])))
        print("  %s" % finding["text"][:160])
    print("VERDICT: %s — %s"
          % (RED if findings else GREEN,
             "repeated paragraph(s) found" if findings else "no repeated paragraph"))


def _self_test(threshold: int) -> int:
    """A controlled failure: the known repetition must read RED.

    The defect this detector was written for is the repetition of one
    paragraph inside a body.  The self-test reproduces that shape, and also
    checks that a clean body and a code-fenced repetition stay GREEN, so a
    detector that answers RED to everything is caught too.
    """
    repeated = ("A paragraph long enough to count as prose worth repeating, "
                "repeated verbatim in the same body by a careless rewrite that "
                "rebuilt the section and re-emitted the closing paragraph the "
                "earlier section had already closed with.")
    cases = (
        ("repeated paragraph", "First.\n\n%s\n\nSecond.\n\n%s" % (repeated, repeated),
         RED),
        ("distinct paragraphs", "First.\n\nSecond.\n\nThird.", GREEN),
        ("repetition inside a code fence",
         "First.\n\n```\n%s\n```\n\nSecond.\n\n```\n%s\n```" % (repeated, repeated),
         GREEN),
    )
    for label, body, expected in cases:
        findings = find_duplicates(body, threshold)
        actual = RED if findings else GREEN
        if actual != expected:
            print("SELF-TEST FAILED: %s read %s, expected %s"
                  % (label, actual, expected))
            return 1
        print("self-test %s: %s" % (label, actual))
    print("VERDICT: GREEN — the controlled repetition was caught")
    return 0


def _live_bodies(env_file: str) -> list[tuple[str, str]]:
    """Every published body on the DEV account, read through the account API.

    The key comes from the site's own credentials file, never from an
    argument, so it never reaches a process list.  A read that fails is an
    error, not an empty account: reporting "no repeated paragraph" from a
    partial read would be the false negative this detector is here to avoid.
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
    base = os.environ.get("MESH_DEVTO_API", "https://dev.to")
    request = urllib.request.Request(
        base + "/api/articles/me/published?per_page=1000",
        headers={"api-key": key, "User-Agent": "mesh-pub/1.0"})
    payload = json.load(urllib.request.urlopen(request, timeout=30))
    if not isinstance(payload, list):
        raise ValueError("the API did not answer with a list of articles")
    pairs = []
    for article in payload:
        if not isinstance(article, dict):
            raise ValueError("the API answered with an article that is not a record")
        body = article.get("body_markdown")
        if not isinstance(body, str):
            raise ValueError("article %r carries no body" % (article.get("id"),))
        pairs.append((str(article.get("id")), body))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--body", default="",
                        help="a single article body to check")
    parser.add_argument("--bodies", default="",
                        help="a JSON capture of article id to body")
    parser.add_argument("--stdin", action="store_true",
                        help="read the body from standard input")
    parser.add_argument("--live", action="store_true",
                        help="read every published body on the DEV account")
    parser.add_argument("--env", default="",
                        help="the credentials file carrying DEVTO_API_KEY")
    parser.add_argument("--self-test", action="store_true",
                        help="run the controlled failure and exit")
    parser.add_argument("--threshold", type=int, default=MIN_PARAGRAPH_CHARS,
                        help="the shortest paragraph that counts (default %d)"
                             % MIN_PARAGRAPH_CHARS)
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test(args.threshold)

    modes = [bool(args.body), bool(args.bodies), bool(args.stdin), bool(args.live)]
    if sum(modes) != 1:
        print("UNKNOWN: exactly one of --body, --bodies, --stdin or --live "
              "is required")
        return 3

    if args.live:
        try:
            pairs = _live_bodies(args.env)
        except (OSError, ValueError, urllib.error.URLError) as error:
            print("UNKNOWN: the live account could not be read: %s" % error)
            return 3
        findings = []
        for identifier, body in pairs:
            for finding in find_duplicates(body, args.threshold):
                findings.append((identifier, finding))
        for identifier, finding in findings:
            print("REPEATED PARAGRAPH in article %s: %d times at paragraphs %s"
                  % (identifier, finding["count"],
                     ", ".join(str(position) for position in finding["positions"])))
            print("  %s" % finding["text"][:160])
        print("VERDICT: %s — %s"
              % (RED if findings else GREEN,
                 "repeated paragraph(s) found in %d of %d published bodies"
                 % (len({identifier for identifier, _ in findings}), len(pairs))
                 if findings else "no repeated paragraph in %d published bodies"
                 % len(pairs)))
        return 2 if findings else 0

    if args.stdin:
        body = sys.stdin.read()
        findings = find_duplicates(body, args.threshold)
        _report(findings, "standard input")
        return 2 if findings else 0

    if args.body:
        try:
            body = Path(args.body).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            print("UNKNOWN: no body read: %s" % error)
            return 3
        findings = find_duplicates(body, args.threshold)
        _report(findings, args.body)
        return 2 if findings else 0

    try:
        data = json.loads(Path(args.bodies).read_text(encoding="utf-8"))
        pairs = _bodies_from_capture(data)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        print("UNKNOWN: no capture read: %s" % error)
        return 3
    findings = []
    for identifier, body in pairs:
        for finding in find_duplicates(body, args.threshold):
            findings.append((identifier, finding))
    for identifier, finding in findings:
        print("REPEATED PARAGRAPH in article %s: %d times at paragraphs %s"
              % (identifier, finding["count"],
                 ", ".join(str(position) for position in finding["positions"])))
        print("  %s" % finding["text"][:160])
    print("VERDICT: %s — %s"
          % (RED if findings else GREEN,
             "repeated paragraph(s) found in %d of %d bodies"
             % (len({identifier for identifier, _ in findings}), len(pairs))
             if findings else "no repeated paragraph in %d bodies" % len(pairs)))
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
