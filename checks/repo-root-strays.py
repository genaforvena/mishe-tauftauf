#!/usr/bin/env python3
"""Detector: no repository-root entry is an unexpanded path literal.

Observed 2026-11-15 21:36Z (health wake 1794783782367667911): the repo root held an
untracked directory literally named ``$MISHE_SEED_HOME`` containing ``walls/docs.md`` --
the docs role's newest wall. The agent that woke docs had passed a shell variable that was
never expanded to its write tool, so the tool made a typo-named tree beside the checkout and
the role's real wall (``SITE/walls/docs.md``) stayed stale. AGENTS.md names exactly this
hazard: never hand-type a ``.mishe-...`` site-home literal, because a tool can create a
wrong-named tree outside CLI validation.

This is not the first instance: artifacts/records prior cases --
``rm-wake69735-stray-literal-seed-home-genome-wall.md`` and the ``repo-root-strays-*`` /
``stray-2026*`` bundles. Each was cleaned up by hand; nothing observed recurrence. This check
is that continuing observation: a repo-root entry whose own name contains ``$`` (an
unexpanded shell variable or command substitution) is a literal path that escaped its shell,
never an intended file.

Scope and limits. It matches the ``$`` signature only, which is the class observed. It does
NOT flag a root entry that merely duplicates a site-home name without a ``$`` (for example a
stray ``operator/``): that signature is ambiguous with an intended directory and would be a
false verdict. It reads the working tree's repository root, which is where the tool writes;
a stray created somewhere else is outside this check.

Exit status:
  0  no repository-root entry name carries a ``$`` literal
  2  one or more repository-root entry names carry a ``$`` literal (names printed)
  3  the repository root cannot be listed

Revert: remove this file and its INVENTORY row in
``SITE/checks-runner/observer-checks.py``.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# A ``$`` in a path segment is a shell variable or command substitution that never expanded
# (``$MISHE_SEED_HOME``) or a literal the shell would have consumed. No tracked or intended
# repo-root entry carries one.
LITERAL_RE = re.compile(r"\$")


def strays(repo: Path) -> tuple[int, list[str]]:
    """``(exit code, verdict lines)`` for the immediate children of ``repo``."""
    try:
        names = sorted(entry.name for entry in repo.iterdir())
    except OSError as exc:
        return 3, [f"repository root unreadable: {exc}"]
    hits = [name for name in names if LITERAL_RE.search(name)]
    if hits:
        return 2, [f"repo-root entry carries an unexpanded path literal: {name}" for name in hits]
    return 0, [f"no unexpanded path literal among {len(names)} repo-root entries"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, help="site home (observer-checks runner contract; unused)")
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args(argv)
    code, lines = strays(args.repo)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
