"""Text boundary for the commit risk class: path-rule matching and git commit output.

The path-rule table and the parsing of ``git show`` output live here, as a named
parsing boundary, so ``commit_risk`` stays a decision module that works on plain
values. Adding a rule means editing ``RULES`` here.
"""
from __future__ import annotations

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
    files = subprocess.run(["git", "-C", str(repo), "show", "--format=", "--name-only", sha], check=True,
                           capture_output=True, text=True).stdout.split()
    return subject, files
