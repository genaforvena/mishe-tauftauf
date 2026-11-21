"""System 0 producer for the repo-change signal (initiative repo-change-signal).

On a new main HEAD the producer reads the commits since each reader's cursor
(pub, docs), classifies their area, drops internal-only noise, asks
:mod:`mishe_tauftauf.repo_change_docs` which reader docs name each
changed source path, writes the per-subscriber delta to ``signals/repo-change.detail``
and publishes the signal (value = newest commit). It calls no model and wakes no one;
the path unit on the signal file wakes the subscribers.

A reader advances its own cursor by calling the producer with ``--wake NAME``
after it has read the detail. A resolver without a cursor can refresh the report.
Missing or unreadable git/repo evidence leaves the signal UNKNOWN
(``observe_unknown``), never GREEN.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from . import signal_store
from .repo_change_docs import docs_naming

SIGNAL = "repo-change"
PUB_SIGNAL = "repo-change-pub"   # reader-meaningful commits only; wakes pub, never the raw signal
MAX_AGE_S = 6 * 3600
REOFFER_S = 3600
SUBSCRIBERS = ("pub", "docs")    # cursor readers; pub is woken by PUB_SIGNAL, docs by SIGNAL
DEFAULT_WINDOW = 50          # commits shown when a subscriber has no cursor yet
RECORD = "\x1e"              # git-log record separator
FIELD = "\x1f"               # git-log field separator
HEADER_END = "\x1d"          # ends the sha/subject/body header; --name-only paths follow it
TRAILER = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):[ \t]+(.+)$")
AREA_PREFIXES = (
    ("src/", "src"), ("tools/", "tools"), ("checks/", "tools"),
    ("duties", "duties"), ("docs/", "docs"), ("tests/", "tests"),
)
NOISE_SUBJECT = re.compile(r"^(?:chore|style|typo|whitespace|pin|pinbump)\b", re.IGNORECASE)


ACK = ("env PYTHONPATH=src python3 -m mishe_tauftauf.repo_change_signal "
       "--home {home} --repo {repo} --wake {role}")


def named_path(path: str) -> bool:
    """Behaviour paths whose reader-doc naming is checked: src modules, tools, checks, duties files.

    Widened from src-only after c5d47a63 (duties.json) changed behaviour yet got no naming
    at all, although operating.md and recovery.md name duties.json.
    """
    return (path.startswith("src/") and path.endswith(".py")) or path.startswith(
        ("tools/", "checks/", "duties"))


def area_for(paths) -> list[str]:
    """Sorted areas touched by a commit; unknown paths are 'other'."""
    areas = set()
    for path in paths:
        name = str(path).lstrip("./")
        area = next((value for prefix, value in AREA_PREFIXES if name.startswith(prefix)), "other")
        areas.add(area)
    return sorted(areas)


def trailers_for(body: str) -> dict:
    """Parse the final paragraph of a commit body as Git trailers."""
    paragraphs = [part for part in re.split(r"\n\s*\n", body.strip()) if part.strip()]
    result = {}
    if paragraphs:
        for line in paragraphs[-1].splitlines():
            match = TRAILER.match(line.strip())
            if match:
                result[match.group(1)] = match.group(2).strip()
    return result


def is_noise(commit: dict) -> bool:
    """Conservative internal-only rule: a chore/style subject or no known-area path.

    A commit with a subject that names behaviour (fix, add, name, detect, ...) is kept
    even when it touches only docs, so a documentation behaviour change can still wake
    a listener. This never decides whether to wake; it only trims the detail view.
    """
    if NOISE_SUBJECT.match(commit.get("subject", "")):
        return True
    return not ({"src", "tools", "duties", "docs", "tests"} & set(commit.get("areas", [])))


def parse_log(text: str) -> list[dict]:
    """Parse ``git log --format=%x1e%H%x1f%s%x1f%b%x1d --name-only`` output.

    The header ends at HEADER_END, so a multi-line body cannot be mistaken for the path list.
    Doc text is opaque data. A record without a valid sha or with no paths is dropped,
    so a torn line can never masquerade as a commit.
    """
    commits = []
    for record in text.split(RECORD)[1:]:
        head, _, files_block = record.partition(HEADER_END)
        fields = head.split(FIELD)
        if len(fields) < 2 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
            continue
        paths = [line.strip() for line in files_block.splitlines() if line.strip()]
        if not paths:
            continue
        commit = {"sha": fields[0], "subject": fields[1].strip(),
                  "body": fields[2].strip() if len(fields) > 2 else "",
                  "paths": paths}
        commit["trailers"] = trailers_for(commit["body"])
        commit["areas"] = area_for(paths)
        commits.append(commit)
    return commits


def read_log(repo: Path, rev_range: list[str]) -> list[dict]:
    """Read commits for a revision range with one bounded git call; empty on no range."""
    if not rev_range:
        return []
    command = ["git", "-C", str(repo), "--no-pager", "log", "--reverse",
               f"--format={RECORD}%H{FIELD}%s{FIELD}%b{HEADER_END}", "--name-only", *rev_range]
    output = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    return parse_log(output.stdout)


def head_sha(repo: Path) -> str:
    output = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=30, check=True)
    sha = output.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("git rev-parse returned no commit id")
    return sha


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    result = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer])
    return result.returncode == 0


def rev_range(repo: Path, cursors: list[str | None], window: int) -> list[str]:
    """Commits after the OLDEST subscriber cursor, so no subscriber misses a commit.

    Any absent, malformed, off-ancestry or diverged cursor falls back to a bounded tail.
    Per-subscriber precision is applied afterwards by :func:`build_report`.
    """
    valid = [c for c in cursors if c and re.fullmatch(r"[0-9a-f]{40}", c)]
    if len(valid) != len(cursors) or not valid:
        return ["HEAD", f"--max-count={window}"]
    oldest = valid[0]
    for cursor in valid[1:]:
        if is_ancestor(repo, cursor, oldest):
            oldest = cursor
        elif not is_ancestor(repo, oldest, cursor):
            return ["HEAD", f"--max-count={window}"]
    if not is_ancestor(repo, oldest, "HEAD"):
        return ["HEAD", f"--max-count={window}"]
    return [f"{oldest}..HEAD"]


def read_cursors(home) -> dict:
    path = Path(home) / "signals" / f"{SIGNAL}-cursors.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"repo-change cursors unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("repo-change cursors must be an object")
    return data


def write_cursors(home, cursors: dict) -> None:
    path = Path(home) / "signals" / f"{SIGNAL}-cursors.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursors, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def build_report(commits: list[dict], cursors: dict, head: str, *, repo_root: Path) -> dict:
    """Per-subscriber deltas with the deterministic docs answer attached to each commit.

    Pure except for reading the reader corpus through ``docs_naming``. A commit with no
    naming doc and no behaviour area is marked 'no_doc_no_behaviour' for docs, which is
    evidence for the residual decision, never an instruction to skip the wake.
    """
    enriched = []
    for commit in commits:
        record = dict(commit)
        record["internal_only"] = is_noise(commit)
        named = [p for p in commit["paths"] if named_path(p)]
        record["docs"] = docs_naming(repo_root, named) if named else None
        record["docs_relevant"] = bool(record["docs"] and record["docs"]["summary"]["named"]) or bool(
            {"src", "tools", "duties"} & set(commit["areas"]))
        enriched.append(record)
    subscribers = {}
    shas = [record["sha"] for record in enriched]
    for name in SUBSCRIBERS:
        cursor = cursors.get(name)
        # The range starts at the oldest cursor and excludes it, so an oldest cursor is absent from
        # the list and everything listed is new to it; a newer cursor is consumed up to its position.
        start = shas.index(cursor) + 1 if cursor in shas else 0
        after = enriched[start:]
        subscribers[name] = {"cursor": cursor, "new": after, "count": len(after)}
    return {"head": head, "head_short": head[:12], "commits": enriched,
            "subscribers": subscribers,
            "summary": {"commits": len(enriched),
                        "internal_only": sum(1 for c in enriched if c["internal_only"]),
                        "pub_new": subscribers["pub"]["count"],
                        "docs_new": subscribers["docs"]["count"]}}


def produce(home, repo, *, wake: str | None = None, window: int = DEFAULT_WINDOW, now: float | None = None) -> dict:
    """Read git + cursors, write the detail, observe the signal. Returns the report."""
    now = time.time() if now is None else now
    home, repo = Path(home), Path(repo)
    cursors = read_cursors(home)
    sha = head_sha(repo)
    # Only cursor readers move a cursor. A resolver watch (declared by sync, e.g. system1) gets the
    # same ack command in its dossier; for it the ack is a read-only refresh and must exit cleanly.
    if wake in SUBSCRIBERS:
        cursors[wake] = sha
        write_cursors(home, cursors)
    commits = read_log(repo, rev_range(repo, [cursors.get(name) for name in SUBSCRIBERS], window))
    report = build_report(commits, cursors, sha, repo_root=repo)
    detail = home / "signals" / f"{SIGNAL}.detail"
    detail.parent.mkdir(parents=True, exist_ok=True)
    detail.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    # Install the dossier command without changing the policy sync declared.
    # Standalone producers still need a registration when there is no duty.
    entry = signal_store.registry(home).get(SIGNAL, {
        "max_age": MAX_AGE_S, "subscribers": SUBSCRIBERS, "reoffer": REOFFER_S,
    })
    if entry.get("ack") != ACK:
        signal_store.register(home, SIGNAL, max_age=entry["max_age"],
                              subscribers=entry.get("subscribers", ()),
                              reoffer=entry.get("reoffer"), wake_on=entry.get("wake_on", "change"),
                              now=now, ack=ACK)
    signal_store.observe(home, SIGNAL, sha[:12], now=now, source="repo-change-producer",
                         event=sha, reason=report["summary"])
    observe_pub(home, report, now=now)
    return report


def pub_verdict(report: dict) -> str | None:
    """Newest commit in pub's delta that is not internal-only, or None when pub owes no read.

    The delta is oldest-first, so the last reader-meaningful entry is the newest one.
    """
    meaningful = [c["sha"] for c in report["subscribers"]["pub"]["new"]
                  if not c["internal_only"] and not docs_only(c)]
    return meaningful[-1] if meaningful else None


def docs_only(commit: dict) -> bool:
    """A commit that touches documentation paths and nothing else. It stays in pub's detail view, but it does not
    wake pub: 2026-11-21, 14 of 16 pub wakes in 5 h were docs-only commits and every one verified that no article claim
    changed. Claim drift against the source is watched by the pub-*-claims duties and the weekly check, not by
    reading every wording change. A commit that also touches src, tools, duties or tests still counts."""
    areas = set(commit.get("areas", []))
    return bool(areas) and areas <= {"docs"}


def observe_pub(home, report: dict, *, now: float) -> None:
    """Publish PUB_SIGNAL: value = the reader-meaningful verdict, unchanged when there is none.

    The stored value changes only when a new reader-meaningful commit arrives, so an internal-only
    commit moves neither the value nor the rev that wakes pub. When there is no verdict the previous
    reading is re-observed unchanged, which refreshes its freshness without an edge. The first
    observation (no verdict yet) records 'none' once.
    """
    if signal_store.registry(home).get(PUB_SIGNAL, {}).get("ack") != ACK:
        signal_store.register(home, PUB_SIGNAL, max_age=MAX_AGE_S, subscribers=("pub",),
                              reoffer=REOFFER_S, now=now, ack=ACK)
    verdict = pub_verdict(report)
    previous = signal_store.recorded(home, PUB_SIGNAL)
    if verdict is not None:
        value, event = verdict[:12], verdict
    elif previous is not None:
        value, event = previous.get("value"), previous.get("event")
    else:
        value, event = "none", None
    signal_store.observe(home, PUB_SIGNAL, value, now=now, source="repo-change-producer",
                         event=event, reason={"pub_new": report["subscribers"]["pub"]["count"],
                                              "pub_meaningful": verdict is not None})


def observe_unknown_all(home, reason: str, *, now: float) -> None:
    """Mark both repo-change signals UNKNOWN; each signal's own failure is recorded separately."""
    for name in (SIGNAL, PUB_SIGNAL):
        try:
            signal_store.observe_unknown(Path(home), name, now=now,
                                         source="repo-change-producer", reason=reason[:300])
        except (ValueError, OSError):
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, help="site home (signals live under it)")
    parser.add_argument("--repo", default=".", help="repository to read commits from")
    parser.add_argument("--wake", help="advance this cursor reader's cursor to HEAD (other roles: read-only ack)")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="tail when a cursor is absent")
    args = parser.parse_args(argv)
    try:
        report = produce(args.home, args.repo, wake=args.wake, window=args.window)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        observe_unknown_all(args.home, str(exc), now=time.time())
        print(f"mishe: repo-change: UNKNOWN: {exc}", file=__import__("sys").stderr)
        return 3
    summary = report["summary"]
    print(f"mishe: repo-change: {report['head_short']} commits={summary['commits']} "
          f"pub_new={summary['pub_new']} docs_new={summary['docs_new']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
