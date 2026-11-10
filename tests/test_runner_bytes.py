"""The observer-check runner's bytes must be carried by the repository.

``SITE/checks-runner/observer-checks.py`` is the program that decides which
checks the plant reads: it holds the ``INVENTORY`` table (the checks it runs)
and the ``NOT_SCHEDULED`` table (the checks it deliberately skips). The
plant's own detector, ``checks/check-scheduler-coverage.py``, reports a
runner whose bytes no commit carries as unreviewable, because such a table
can change with no diff, no commit and no review, and nothing observes the
change.

Measured on this plant (2026-11-10): the runner's inventory had changed since
its last backup — five checks stopped being read and five started — with no
diff anywhere, because the file lived only under the gitignored site home.

These tests pin the two facts that make the schedule reviewable:

* the runner's source is in the repository, at ``checks-runner/observer-checks.py``;
* where the plant's site home is readable, the live runner's bytes are the
  committed bytes, so the table the plant runs is the table a reader reviews.

The second test skips when no site home is visible, which is the case in CI.
It never compares against a recorded hash that could drift: it compares the
live file against the committed file, so one of the two is always the truth.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED_RUNNER = REPO / "checks-runner" / "observer-checks.py"

#: The site home of the plant, when the test runs on the plant itself.
SITE_HOME = Path(os.environ.get("MISHE_SEED_HOME", "")) if os.environ.get("MISHE_SEED_HOME") else None


def test_the_runners_source_is_in_the_repository():
    """The schedule the plant runs must be readable in a reviewed diff."""
    assert COMMITTED_RUNNER.is_file(), (
        "the observer-check runner's source is not in the repository at %s, so the "
        "table that decides which checks the plant reads can change with no diff, "
        "no commit and no review" % COMMITTED_RUNNER.relative_to(REPO))
    source = COMMITTED_RUNNER.read_text(encoding="utf-8")
    assert "INVENTORY" in source, "the committed runner carries no INVENTORY table"
    assert "NOT_SCHEDULED" in source, "the committed runner carries no NOT_SCHEDULED table"


def test_the_live_runner_is_the_committed_runner():
    """Where the plant's home is readable, the live bytes are the reviewed bytes."""
    if SITE_HOME is None:
        return
    live = SITE_HOME / "checks-runner" / "observer-checks.py"
    if not live.is_file():
        return
    committed = COMMITTED_RUNNER.read_bytes()
    installed = live.read_bytes()
    assert hashlib.sha256(installed).digest() == hashlib.sha256(committed).digest(), (
        "the live observer-check runner (%s) is not the committed runner (%s); the "
        "checks the plant reads are decided by bytes no commit carries"
        % (hashlib.sha256(installed).hexdigest()[:12],
           hashlib.sha256(committed).hexdigest()[:12]))
