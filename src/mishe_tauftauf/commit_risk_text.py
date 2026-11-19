r"""Text boundary for the commit risk class: path-rule matching and git commit output.

The path-rule table and the parsing of ``git show`` output live here, as a named
parsing boundary, so ``commit_risk`` stays a decision module that works on plain
values. Adding a rule means editing ``RULES`` here.
End-anchored rules use `\Z`, not `$`: Python `$` also matches just before a
final newline, and `changed_paths` preserves newline-containing Git filenames.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

RULES = (
    # delivery_requests.py is delivery work (enqueue/consume, patch/revert phases,
    # release_promotion), not dispatch. Listing it in the dispatch pattern below used to
    # shadow the delivery pattern's deliver\w*, which would otherwise claim it. Two confident
    # model labels on that path (1.0, 0.89) agree it is delivery-path.
    ("dispatch-path", re.compile(r"^src/mishe_tauftauf/(wake|engine_\w+|owed|access|"
                                 r"work_state|dispositions|failure|host_capacity)\w*\.py\Z")),
    ("delivery-path", re.compile(r"^(src/mishe_tauftauf/(release|deliver|delivery|heal|promotion|pin)\w*\.py"
                                 r"|scripts/.*(deliver|heal|release)\S*)")),
    ("delivery-path", re.compile(r"^tools/delivery_check\.py\Z")),
    ("ordinary", re.compile(r"^tools/network_view\.py\Z")),
    ("routine", re.compile(r"^(docs/|tests/|checks/|.*\.md\Z)")),
)


def path_label(path: str) -> str | None:
    """The label of the first rule that matches ``path``, or None when no rule does."""
    return next((label for label, rx in RULES if rx.search(path)), None)


def changed_paths(repo, sha: str) -> tuple[str, list[str]]:
    """Return ``(subject, changed paths)`` for one commit."""
    subject = subprocess.run(["git", "-C", str(repo), "show", "-s", "--format=%s", sha], check=True,
                             capture_output=True, text=True).stdout.strip()
    # -z keeps paths with spaces or newlines intact; split on NUL, not whitespace.
    files = subprocess.run(["git", "-C", str(repo), "show", "-z", "--format=", "--name-only", sha], check=True,
                           capture_output=True, text=True).stdout.split("\0")
    return subject, [path for path in files if path]


def recent_shas(repo, count: int) -> list[str]:
    """Up to ``count`` commit SHAs reachable from HEAD, oldest first."""
    out = subprocess.run(["git", "-C", str(repo), "rev-list", "--reverse", f"-n{int(count)}", "HEAD"], check=True,
                         capture_output=True, text=True).stdout
    return [sha for sha in out.split() if sha]


def ledger_shas(ledger: pathlib.Path) -> set[str]:
    """SHAs already answered in a commit-risk ledger; an absent ledger has none."""
    if not ledger.exists():
        return set()
    return {json.loads(line)["sha"] for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()}


def duty_args(argv=None):
    """The advisory duty's own flags: --home SITE and --repo CHECKOUT (both required)."""
    import argparse
    parser = argparse.ArgumentParser(description="Advisory commit-risk pass (never gates delivery).")
    parser.add_argument("--home", type=pathlib.Path, required=True)
    parser.add_argument("--repo", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def load_rows(ledger: pathlib.Path) -> list[dict]:
    """Ledger rows as dicts; an absent ledger has none."""
    rows = []
    if not ledger.exists():
        return rows
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


_MARK = "COMMIT\x00"
FIX_PREFIXES = ("fix", "revert")


def is_fix(subject: str) -> bool:
    """True when a commit subject is a fix or a revert."""
    return subject.lower().startswith(FIX_PREFIXES)


def parse_git_log(text: str) -> list[dict]:
    """Parse ``git log --name-only`` output written with the ``_MARK`` format."""
    commits: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith(_MARK):
            _, sha, ct, subject = line.split("\x00", 3)
            current = {"sha": sha, "ct": int(ct), "subject": subject, "paths": set()}
            commits.append(current)
        elif line.strip() and current is not None:
            current["paths"].add(line.strip())
    return commits


def git_commits(repo: pathlib.Path) -> list[dict]:
    """Every commit in the checkout with its changed paths, parsed from git log."""
    out = subprocess.run(
        ["git", "log", "--all", "--name-only",
         "--format=COMMIT%x00%H%x00%ct%x00%s"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    return parse_git_log(out)


def outcome_args(argv=None):
    """The measurement's flags: --repo CHECKOUT and --ledger PATH (both required), optional --now."""
    import argparse
    parser = argparse.ArgumentParser(description="Read-only commit-risk ground-truth measurement.")
    parser.add_argument("--repo", required=True, type=pathlib.Path)
    parser.add_argument("--ledger", required=True, type=pathlib.Path)
    parser.add_argument("--now", type=float, default=None)
    return parser.parse_args(argv)
