"""Text boundary for the commit risk class: path-rule matching and git commit output.

The path-rule table and the parsing of ``git show`` output live here, as a named
parsing boundary, so ``commit_risk`` stays a decision module that works on plain
values. Adding a rule means editing ``RULES`` here.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

RULES = (
    ("dispatch-path", re.compile(r"^src/mishe_tauftauf/(wake|engine_policy|engine_router|engine_usage|owed|access|"
                                 r"delivery_requests|work_state|dispositions|failure|host_capacity)\w*\.py$")),
    ("delivery-path", re.compile(r"^(src/mishe_tauftauf/(release|deliver|delivery|heal|promotion|pin)\w*\.py"
                                 r"|scripts/.*(deliver|heal|release)\S*)")),
    ("routine", re.compile(r"^(docs/|tests/|.*\.md$)")),
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
