#!/usr/bin/env python3
"""Detector: no repository-root entry is a site-home literal.

Observed 2026-11-15 21:36Z (health wake 1794783782367667911): the repo root held an
untracked directory literally named ``$MISHE_SEED_HOME`` containing ``walls/docs.md`` --
the docs role's newest wall. The agent that woke docs had passed a shell variable that was
never expanded to its write tool, so the tool made a typo-named tree beside the checkout and
the role's real wall (``SITE/walls/docs.md``) stayed stale. AGENTS.md names exactly this
hazard: never hand-type a ``.mishe-...`` site-home literal, because a tool can create a
wrong-named tree outside CLI validation.

Two name shapes reach this check, and the second is the one that actually recurred:

1. an unexpanded shell variable or command substitution (``$MISHE_SEED_HOME``) -- the
   literal the shell would have consumed;
2. a misspelled reserved name (``.mishe-tauftauft``) -- hand-typed, so no ``$`` survives.
   Observed twice and undisposed for a month: ``.mishe-tauftauft-notes.txt`` (2026-10-15,
   byte-identical to ``.mishe-tauftauf-notes.txt``) and ``.mishe-tauftauft-notes-65743.md``
   (2026-10-19). ``.gitignore`` covers ``.mishe-*``, so both were invisible to ``git status``
   and to ``sense.repo.branch-inventory``'s ``strays`` field, and nothing routed them.

Scope and limits. The reserved-prefix rule fires only when ``--home`` is given, and only on a
root entry that starts with ``.mishe-`` and is not the site home itself; the one legitimate
``.mishe-*`` entry at a checkout root is the plant's own home. It does NOT flag a root entry
that merely duplicates a site-home name with no ``.mishe-`` prefix (for example a stray
``operator/``): that signature is ambiguous with an intended directory and would be a false
verdict. It reads the working tree's repository root, which is where the tool writes; a stray
created somewhere else is outside this check.

This is the routed reading of the class. ``sense.site.ghost-homes`` is the descriptive one:
it counts ghost *directories*, ages them and names which shapes Git would stage, and it stays
``verified`` by design because a stray file cannot become a site home. A role that only reads
the dashboard therefore learns the name but is never woken, which is how the two files above
survived a month. This check answers the narrower question the runner routes on.

Exit status:
  0  no repository-root entry is a site-home literal
  2  one or more repository-root entries are site-home literals (names printed)
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

# The reserved site-home prefix. ``.gitignore`` covers ``.mishe-*``, so an entry under it is
# invisible to Git; the plant's own home is the only one that belongs at a checkout root.
SITE_HOME_PREFIX = ".mishe-"


def strays(repo: Path, home: Path | None = None) -> tuple[int, list[str]]:
    """``(exit code, verdict lines)`` for the immediate children of ``repo``.

    ``home`` is the plant's site home. Without it only the ``$`` shape is read, so a caller
    that has no site home gets the original narrow contract rather than a guess.
    """
    try:
        names = sorted(entry.name for entry in repo.iterdir())
    except OSError as exc:
        return 3, [f"repository root unreadable: {exc}"]
    home_name = home.name if home is not None else None
    hits: list[str] = []
    for name in names:
        if LITERAL_RE.search(name):
            hits.append(f"repo-root entry carries an unexpanded path literal: {name}")
        elif home_name is not None and name.startswith(SITE_HOME_PREFIX) and name != home_name:
            hits.append("repo-root entry is a site-home literal outside the site home: "
                        f"{name} (site home is {home_name})")
    if hits:
        return 2, hits
    if home_name is None:
        return 0, [f"no unexpanded path literal among {len(names)} repo-root entries"]
    return 0, [f"no site-home literal among {len(names)} repo-root entries"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path,
                        help="site home; without it only the unexpanded-variable shape is read")
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args(argv)
    home = args.home.resolve() if args.home is not None else None
    code, lines = strays(args.repo, home)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
