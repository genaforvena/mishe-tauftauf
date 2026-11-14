#!/usr/bin/env python3
"""Detector: no live or draft DEV.to text carries a private identifier.

The rule list was confirmed by Ilya on 2026-11-12 13:46Z ("ок") and delegated
to Mishe at 15:37Z/15:46Z.  Four rules cover what may not leave the plant:

  R1 no real names, email addresses, project/site names, household members,
     locations, device identities or account handles;
  R2 no internal ids, wall text, chat logs or raw JSON;
  R3 no copied private drafts or sensor/coordinate data;
  R4 no outward secret or non-generic client identity.

Why this check is a committed file and not a site-local script.  It used to
live at ``SITE_HOME/pub/bin/pub-privacy-scan``, outside the repository, and
the delivery gate's reviewer could not see it: on 2026-11-14 the review of the
duty that runs this check refused with "insufficient context to assess deeper
defects", naming the scanner's exit-code contract as missing evidence.  A
reviewer can only judge bytes it is given, so a check whose contract lives
outside the repository can never be reviewed.  Its three siblings —
``pub-index-self-claims.py``, ``pub-account-count-claims.py`` and
``pub-article-duplication.py`` — are all committed under ``checks/``; this one
is now the fourth.

The private term lists (names, places, devices, handles) stay site-local in
``SITE_HOME/pub/privacy-terms.json`` and are never committed: they are the
names, not the rule.  This file holds the structural patterns and the rule
logic, both of which are exactly what a reviewer needs to see.

Verdicts: GREEN when every item was read and none violated a rule, RED when
one did, UNKNOWN when an item could not be read.  A text that cannot be read
is UNKNOWN, never a pass.  Exit codes: 0 GREEN, 2 RED, 3 UNKNOWN.

The verdict reaches the plant bus as well as the report file, because a scan
whose result lives only in a file is invisible to the bus.  On 2026-11-13 the
report file said GREEN at 22:25Z while the signal still said RED from 20:35Z,
because the run was made by hand and never reached the bus.  So every ``--all``
run publishes its own verdict through the canonical CLI, and a run that cannot
publish it says so and leaves the signal alone (never a silent GREEN).

Output never contains the matched text: only the item id, the field and the
rule code.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"

API = "https://dev.to/api"
MAX_AGE_SECONDS = 86400  # a report older than this is UNKNOWN for the signal
SIGNAL_NAME = "pub-privacy-scan"
SELF_TEST_SIGNAL = "pub-privacy-scan-selftest"

#: The site home.  Every site-local path is read from here, so the check never
#: guesses a location from its own working directory.  It is read from the
#: environment because the plant's own units bind it, and a test run may bind a
#: disposable site instead: every site-local path below is also named by an
#: argument, so the check never depends on this default.
SITE = Path(os.environ.get("MISHE_SEED_HOME", Path(__file__).resolve().parents[1]))

#: The site-local rule terms.  Never committed: these are the names, not the rule.
TERMS_FILE = SITE / "pub" / "privacy-terms.json"

#: The report the scan writes, site-local and never committed.
REPORT = SITE / "pub" / "privacy-scan.json"

#: The credentials file.  It holds DEVTO_API_KEY; the key never reaches a
#: process list, so it is read from a file and never taken as an argument.
ENV_FILE = Path(os.environ.get("MESH_DEVTO_ENV", str(SITE / "pub" / "access" / "devto.env")))

#: The operator CLI used to publish the verdict.
CLI = SITE / "bin" / "mishe"


# ---- R2/R3/R4 structural patterns (no private terms here) -------------------------
STRUCTURAL = [
    ("R1", "email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("R1", "ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("R1", "mac", re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")),
    # lat/lon pairs: nonzero integer part and 4+ decimals; statistical intervals like [0.153, 0.198] do not match
    ("R3", "coordinates", re.compile(r"-?[1-9]\d{0,2}\.\d{4,}\s*,\s*-?[1-9]\d{0,2}\.\d{4,}")),
    ("R2", "internal-id", re.compile(r"#\d{5,}\b|\b(?:dm|chat|inbox|wake)[ -]?#?\d{6,}\b|\bwake-[a-z]+-\d{6,}")),
    ("R2", "wall-marker", re.compile(r"^#{1,3}\s*(?:Plate|Next command|What would change my mind|Recent factual outcomes|Missing next actions)\b", re.M)),
    ("R2", "chat-log", re.compile(r"^\s*\|\s*\[dm\]|\bv1 0{10,}", re.M)),
    ("R4", "secret", re.compile(r"DEVTO_API_KEY|api[-_ ]?key\s*[:=]|Bearer\s+[A-Za-z0-9._-]{16,}", re.I)),
    ("R4", "client-identity", re.compile(r"User-Agent\s*[:=]", re.I)),
]
RAW_JSON_KEY = re.compile(r'"[A-Za-z_][A-Za-z0-9_]*"\s*:\s*("|\d|\[|\{|true\b|false\b|null\b)')

# A sha/digest is an internal id unless it is a reference to someone else's public code.
# Two references are allowed (decisions recorded 2026-11-13, operator/standing-approvals.md):
#   - a sha that is part of a URL into a public repository;
#   - a sha inside a FENCED code block, which is quoted program output, so the sha belongs
#     to that program's output and not to our internal id space. An INLINE code span
#     (``` `digest` ```) is our own prose and is still a violation.
SHA_TOKEN = re.compile(r"\b[0-9a-f]{40}\b|\b[0-9a-f]{64}\b")
FENCED_BLOCK = re.compile(r"^```.*?^```", re.S | re.M)


def strip_fenced_blocks(text: str) -> str:
    """Remove fenced code blocks so their contents are not scanned as our own prose."""
    return FENCED_BLOCK.sub("", text)


def sha_violations(text: str, public_repo_rx: list) -> bool:
    """True when a sha/digest in `text` is an internal id rather than a public reference."""
    if not SHA_TOKEN.search(text):
        return False
    if any(rx.search(text) for rx in public_repo_rx):
        # The whole body names a public repository: a sha next to it is that repo's commit.
        for m in SHA_TOKEN.finditer(text):
            window = text[max(0, m.start() - 260):m.end() + 60]
            if not any(rx.search(window) for rx in public_repo_rx):
                return True
        return False
    return True


def load_terms(terms_file: Path | None = None) -> dict:
    """Return {rule: [(code, compiled_regex)]} from the site-local term file.

    A missing or unreadable term file is an error (the caller reports UNKNOWN), never an
    empty list that would quietly pass everything.
    """
    raw = json.loads(Path(terms_file or TERMS_FILE).read_text(encoding="utf-8"))
    out = []
    for group in ("names", "places", "devices", "projects", "handles", "household"):
        for term in raw.get(group, []):
            out.append(("R1", group, re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", re.I)))
    for term in raw.get("sensor", []):
        out.append(("R3", "sensor-term", re.compile(re.escape(term), re.I)))
    allowed = raw.get("allowed", {})
    own = allowed.get("own_handle", [])
    # A dev.to link to the account's OWN posts is allowed (canonical_url decision, 2026-11-13).
    # A link to somebody else's handle is not: the pattern matches any handle EXCEPT ours.
    foreign = []
    for handle in own:
        foreign.append(re.compile(r"dev\.to/(?!" + re.escape(handle) + r")[A-Za-z0-9_]+_[0-9a-f]{8,}"))
    if not foreign:
        foreign.append(re.compile(r"dev\.to/[A-Za-z0-9_]+_[0-9a-f]{8,}"))
    public_repo_rx = [re.compile(re.escape(u)) for u in allowed.get("public_repo_urls", [])]
    return {"terms": out, "foreign_handles": foreign, "public_repo_rx": public_repo_rx}


def scan_text(text: str, terms: list, foreign_handles: list = (), public_repo_rx: list = ()) -> list[tuple[str, str]]:
    """Return [(rule, code)] for violations. Never returns matched text."""
    hits: list[tuple[str, str]] = []
    for rule, code, rx in STRUCTURAL:
        if rx.search(text):
            hits.append((rule, code))
    # Raw JSON is a violation in our own prose (an internal state dump). A fenced code block is
    # quoted program output, the same exemption the sha rule already takes (decision recorded
    # 2026-11-13, operator/standing-approvals.md): the JSON belongs to the program, not to our
    # internal evidence space. Every other rule still reads the fenced content, so a device
    # name, a wake id, an email or a secret inside a block stays RED.
    if len(RAW_JSON_KEY.findall(strip_fenced_blocks(text))) >= 2:
        hits.append(("R2", "raw-json"))
    for rule, code, rx in terms:
        if rx.search(text):
            hits.append((rule, code))
    for rx in foreign_handles:
        if rx.search(text):
            hits.append(("R1", "dev-handle"))
            break
    if sha_violations(strip_fenced_blocks(text), list(public_repo_rx)):
        hits.append(("R2", "sha-or-digest"))
    return sorted(set(hits))


def text_or_unknown(text) -> tuple[bool, str]:
    if not isinstance(text, str):
        return False, ""
    return True, text


# ---- DEV reads (GET only) ----------------------------------------------------------
def read_key(env_file: str) -> str:
    """The account key, from the site's own credentials file.

    The key never comes from an argument, so it never reaches a process list.
    A credentials file that cannot be read is an error, never an empty key:
    reporting "clean" from an account that could not be read would be the
    false negative this check exists to avoid.
    """
    key = os.environ.get("DEVTO_API_KEY", "")
    if not key and env_file:
        try:
            for line in Path(env_file).read_text(encoding="utf-8").splitlines():
                if line.startswith("DEVTO_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
                    break
        except (OSError, UnicodeDecodeError):
            key = ""
    if not key:
        raise ValueError("DEVTO_API_KEY missing")
    return key


def get(path: str, key: str | None = None):
    headers = {"User-Agent": "mesh-pub/1.0"}
    if key:
        headers["api-key"] = key
    req = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)


def page_rows(path: str, key: str) -> list[dict]:
    rows_out, page = [], 1
    while True:
        rows = get(f"{path}?per_page=100&page={page}", key)
        if not rows:
            return rows_out
        rows_out.extend(rows)
        if len(rows) < 100:
            return rows_out
        page += 1


def collect(key: str) -> list[dict]:
    items: list[dict] = []
    sources = [("live", "/articles/me/published"), ("draft", "/articles/me/unpublished")]
    seen: set[int] = set()
    for state, path in sources:
        for row in page_rows(path, key):
            aid = row["id"]
            if aid in seen:
                continue
            seen.add(aid)
            # DEV answers 404 for GET /articles/{id} on unpublished drafts, but the
            # unpublished listing already carries body_markdown, title and tag_list.
            item = {"id": aid, "state": state}
            if state == "draft":
                item["row"] = row
            items.append(item)
            time.sleep(0.4)
    return items


def read_item(item: dict, key: str) -> dict:
    """Fetch body, title, tags and replies for one article. Unreadable parts → UNKNOWN."""
    fields: dict[str, object] = {}
    try:
        art = item["row"] if "row" in item else get(f"/articles/{item['id']}", key)
        fields["title"] = art.get("title")
        fields["body"] = art.get("body_markdown")
        fields["tags"] = " ".join(art.get("tag_list") or []) if isinstance(art.get("tag_list"), list) else art.get("tag_list")
    except (urllib.error.URLError, ValueError, KeyError, TypeError) as err:
        fields["_read_error"] = type(err).__name__
    time.sleep(0.4)
    replies = []
    try:
        for c in get(f"/comments?a_id={item['id']}"):
            replies.append(html.unescape(re.sub(r"<[^>]+>", " ", c.get("body_html") or "")))
        fields["replies"] = replies
    except (urllib.error.URLError, ValueError, TypeError) as err:
        fields["_reply_error"] = type(err).__name__
    time.sleep(0.4)
    return fields


def verdict_for(fields: dict, terms: list, foreign_handles: list = (), public_repo_rx: list = ()) -> tuple[str, list[tuple[str, str, str]]]:
    """Return (verdict, violations) where violations are (field, rule, code)."""
    violations: list[tuple[str, str, str]] = []
    unknown = False
    if "_read_error" in fields:
        unknown = True
    if "_reply_error" in fields:
        unknown = True
    for name in ("title", "body", "tags"):
        ok, text = text_or_unknown(fields.get(name))
        if not ok:
            unknown = True
            continue
        for rule, code in scan_text(text, terms, foreign_handles, public_repo_rx):
            violations.append((name, rule, code))
    for n, reply in enumerate(fields.get("replies") or []):
        for rule, code in scan_text(reply, terms, foreign_handles, public_repo_rx):
            violations.append((f"reply[{n}]", rule, code))
    if violations:
        return RED, violations
    if unknown:
        return UNKNOWN, []
    return GREEN, []


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_verdict(verdict: str, detail: str, *, signal: str = SIGNAL_NAME, cli: Path | None = None) -> bool:
    """Record this scan's verdict on the plant bus.

    The report file and the bus are two readers of one fact, so the scan writes
    both. Publishing is done through the canonical CLI (``mishe signal observe``),
    which is the only writer that also re-arms the expiry timer and lets the
    subscriber path trigger fire; writing the signal file by hand would refresh
    the reading without waking anybody.

    An UNKNOWN reading is recorded with ``observe-unknown``, because "UNKNOWN" is
    a state and not a reading: observing it as a value would store a fresh OK
    reading and hide the very failure it names.

    Returns True when the bus carries the verdict. False means it could not be
    published, which the caller reports; the signal is then left as it was, which
    is UNKNOWN or RED rather than a false GREEN.
    """
    if cli is None:
        cli = SITE / "bin" / "mishe"
    if not cli.exists():
        print(f"pub-privacy-scan: no mishe CLI at {cli}; the signal was not published")
        return False
    if verdict == UNKNOWN:
        cmd = [str(cli), "signal", "observe-unknown", signal,
               "--reason", detail[:200], "--source", "run:checks/pub-privacy-scan.py"]
    else:
        cmd = [str(cli), "signal", "observe", signal, verdict,
               "--source", "run:checks/pub-privacy-scan.py"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=dict(os.environ))
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"pub-privacy-scan: could not publish {verdict} to the signal: {type(exc).__name__}")
        return False
    if proc.returncode != 0:
        print(f"pub-privacy-scan: could not publish {verdict} to the signal: "
              f"exit {proc.returncode} {(proc.stderr or '').strip()[:200]}")
        return False
    print(f"pub-privacy-scan: signal {signal} = {verdict} ({proc.stdout.strip()})")
    return True


def self_test(terms_file: Path | None = None) -> int:
    """Controlled failure: a known violation must be RED; clean and unreadable must not pass.

    The cases below also pin the 2026-11-13 decisions: the account's OWN handle and a sha
    that is a public-repository reference are allowed, while a FOREIGN handle, an inline
    internal digest and a wake id stay RED.
    """
    loaded = load_terms(terms_file)
    terms = loaded["terms"]
    foreign_handles = loaded["foreign_handles"]
    public_repo_rx = loaded["public_repo_rx"]
    own = "https://dev.to/ilya_mozerov_867dbdd91feb/some-post-1abc"
    foreign = "https://dev.to/someone_else_99aa11bb22/some-post-2xyz"
    cases = {
        "clean": ({"title": "A plain note", "body": "A sentence about a gate.", "tags": "dev", "replies": ["Thanks."]}, GREEN),
        "known-violation": ({"title": "x", "body": "Contact probe@example.com for the plan.", "tags": "", "replies": []}, RED),
        "unreadable": ({"title": "x", "_read_error": "HTTPError"}, UNKNOWN),
        # decisions: own handle allowed
        "own-handle-allowed": ({"title": "x", "body": f"See {own} for the post.", "tags": "", "replies": []}, GREEN),
        # decisions: a foreign handle is not allowed
        "foreign-handle-red": ({"title": "x", "body": f"See {foreign} for the post.", "tags": "", "replies": []}, RED),
        # decisions: a sha that is a public repository reference is allowed
        "public-repo-sha-allowed": ({"title": "x", "body": "Read https://github.com/genaforvena/lte-workstation/commit/d67bd016231519e618a39537a3a25a247e531b73 for the notice.", "tags": "", "replies": []}, GREEN),
        # an internal digest in our own prose is still a violation
        "inline-digest-red": ({"title": "x", "body": "digest 19a674ad01efeb22e3e68925ea93d09f3f4bf0882894d282d8615028a0606bdf of the scan", "tags": "", "replies": []}, RED),
        # a sha inside a fenced block is quoted program output, not our internal id
        "fenced-sha-allowed": ({"title": "x", "body": "```\ntailscale commit: 6cac918179d4d673bfebe2fc74f81183ddd73fea\n```", "tags": "", "replies": []}, GREEN),
        # raw JSON in our own prose is an internal state dump and stays a violation
        "inline-json-red": ({"title": "x", "body": 'the state file was {"paxos": {"ballot": 1, "leader": "a"}, "last_chosen": "blue"}', "tags": "", "replies": []}, RED),
        # raw JSON inside a fenced block is quoted program output
        "fenced-json-allowed": ({"title": "x", "body": '```json\n{"paxos": {"ballot": 1},\n "last_chosen": "blue"}\n```', "tags": "", "replies": []}, GREEN),
        # an internal wake id stays a violation
        "wake-id-red": ({"title": "x", "body": "Corrected in wake 1794413465868601864.", "tags": "", "replies": []}, RED),
        # a device name stays a violation
        "device-red": ({"title": "x", "body": "It ran on the mesh-home box.", "tags": "", "replies": []}, RED),
    }
    ok = True
    for name, (fields, expected) in cases.items():
        got, _ = verdict_for(fields, terms, foreign_handles, public_repo_rx)
        print(f"self-test {name}: expected {expected} got {got} {'OK' if got == expected else 'FAIL'}")
        ok = ok and got == expected
    return 0 if ok else 3


def run_all(key: str, *, terms_file: Path | None = None, report: Path | None = None,
            cli: Path | None = None) -> int:
    loaded = load_terms(terms_file)
    terms = loaded["terms"]
    foreign_handles = loaded["foreign_handles"]
    public_repo_rx = loaded["public_repo_rx"]
    items = collect(key)
    results = []
    for item in items:
        fields = read_item(item, key)
        verdict, violations = verdict_for(fields, terms, foreign_handles, public_repo_rx)
        results.append({"id": item["id"], "state": item["state"], "verdict": verdict,
                        "violations": [{"field": f, "rule": r, "code": c} for f, r, c in violations]})
    counts = {v: sum(1 for r in results if r["verdict"] == v) for v in (GREEN, RED, UNKNOWN)}
    overall = RED if counts[RED] else (UNKNOWN if counts[UNKNOWN] else GREEN)
    report_path = Path(report or REPORT)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"checked_at": now_iso(), "max_age_seconds": MAX_AGE_SECONDS, "overall": overall,
               "counts": counts, "items": results}
    tmp = report_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, report_path)
    print(f"pub-privacy-scan {overall} items={len(results)} {counts}")
    # The bus must carry this verdict, not only the report file. When it cannot, the run
    # is UNKNOWN rather than a silent GREEN: the signal keeps its old reading and ages
    # to UNKNOWN on its own, which is the honest state for "we scanned but could not
    # say so on the bus".
    #
    # A RED stays RED even when the publish fails. A violation we observed is an
    # observation, not a missing one, and the producer maps exit 2 onto the bus
    # itself; reporting UNKNOWN instead would mask a real violation behind a
    # transport failure and let the lane read as "nothing known" rather than
    # "something wrong".
    if not publish_verdict(overall, f"scan {overall}: {counts}", cli=cli):
        return 2 if overall == RED else 3
    return {GREEN: 0, RED: 2, UNKNOWN: 3}[overall]


def scan_file(path: str, terms_file: Path | None = None) -> int:
    loaded = load_terms(terms_file)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        print("UNKNOWN: draft unreadable")
        return 3
    fields = {"title": text.splitlines()[0] if text else "", "body": text, "tags": "", "replies": []}
    verdict, violations = verdict_for(fields, loaded["terms"], loaded["foreign_handles"], loaded["public_repo_rx"])
    print(f"{verdict} " + " ".join(f"{f}:{r}/{c}" for f, r, c in violations))
    return {GREEN: 0, RED: 2, UNKNOWN: 3}[verdict]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true",
                        help="fetch every live and draft article and reply, scan, write the report")
    parser.add_argument("--file", default="",
                        help="scan one draft file before a publish")
    parser.add_argument("--self-test", action="store_true",
                        help="run the controlled failure and exit")
    parser.add_argument("--env", default=str(ENV_FILE),
                        help="the credentials file carrying DEVTO_API_KEY")
    parser.add_argument("--terms", default=str(TERMS_FILE),
                        help="the site-local rule terms file")
    parser.add_argument("--report", default=str(REPORT),
                        help="where the scan writes its report")
    parser.add_argument("--cli", default=str(CLI),
                        help="the operator CLI used to publish the verdict")
    args = parser.parse_args(argv)

    terms_file = Path(args.terms)
    report = Path(args.report)
    cli = Path(args.cli)

    if args.self_test:
        try:
            return self_test(terms_file)
        except (OSError, ValueError) as error:
            print(f"UNKNOWN: the rule terms could not be read ({error})")
            return 3

    try:
        key = read_key(args.env)
    except (OSError, ValueError) as error:
        print(f"UNKNOWN: the account could not be read ({error})")
        return 3

    if args.all:
        try:
            return run_all(key, terms_file=terms_file, report=report, cli=cli)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"UNKNOWN: the account could not be read ({error})")
            return 3
    if args.file:
        try:
            return scan_file(args.file, terms_file)
        except (OSError, ValueError) as error:
            print(f"UNKNOWN: the rule terms could not be read ({error})")
            return 3
    parser.print_help()
    return 3


if __name__ == "__main__":
    sys.exit(main())
