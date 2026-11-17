"""The observer-check runner's bytes must be carried by a reviewed tree.

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
  bytes of the release the plant runs (what ``runtime/current`` names), so the
  table the plant runs is the table a reader reviews.

``mishe sync`` installs the live path as a link to that pinned copy, so the
second fact holds without anyone copying a file (2026-11-17: a hand copy from
the previous day made promotion of ``0e67fdb1`` fail here). The test stays
because the link can be replaced by a copy again, and a copy is the drift this
compares for.

The second test deliberately does not compare against HEAD. Activation copies
the release tree and then advances the pointer, so a commit that changes the
runner cannot have its bytes installed until the pin contains that commit.
Comparing live against HEAD made every runner-changing commit fail its own
delivery check — the check ran inside the promotion, could not pass until the
pin advanced, and the pin could not advance until the check passed (measured
2026-11-17: pin at ``05da8d15``, live ``43f980fd``, HEAD copy ``9a84df2f``,
candidate ``0e67fdb1`` failing on this line). The pinned copy is itself
commit-carried, so the property is unchanged; only the activation lag is now
legal.

Neither test compares against a recorded hash that could drift: each compares
two files that exist now, so one of the two is always the truth.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED_RUNNER = REPO / "checks-runner" / "observer-checks.py"

#: The site home of the plant, when the test runs on the plant itself.
SITE_HOME = Path(os.environ["MISHE_SEED_HOME"]) if os.environ.get("MISHE_SEED_HOME") else None


def pinned_release(site_home: Path) -> Path | None:
    """The release the plant runs: the target of ``runtime/current``, when it exists."""
    link = site_home / "runtime" / "current"
    return link.resolve() if link.exists() else None


def reviewed_runner(repo: Path, site_home: Path | None) -> Path:
    """The runner copy whose bytes the plant is supposed to be running.

    That is the pinned release's copy when a pin is readable and carries the
    runner. HEAD's copy stands in only when the site home is not visible, has
    no pin, or the pin's release predates ``checks-runner/``; a release that
    should carry the runner but does not is pin-integrity's RED, not a reason
    to pass here.
    """
    if site_home is not None:
        release = pinned_release(site_home)
        if release is not None:
            candidate = release / "checks-runner" / "observer-checks.py"
            if candidate.is_file():
                return candidate
    return repo / "checks-runner" / "observer-checks.py"


def digest(path: Path) -> bytes:
    return hashlib.sha256(path.read_bytes()).digest()


def test_the_runners_source_is_in_the_repository():
    """The schedule the plant runs must be readable in a reviewed diff."""
    assert COMMITTED_RUNNER.is_file(), (
        "the observer-check runner's source is not in the repository at %s, so the "
        "table that decides which checks the plant reads can change with no diff, "
        "no commit and no review" % COMMITTED_RUNNER.relative_to(REPO))
    source = COMMITTED_RUNNER.read_text(encoding="utf-8")
    assert "INVENTORY" in source, "the committed runner carries no INVENTORY table"
    assert "NOT_SCHEDULED" in source, "the committed runner carries no NOT_SCHEDULED table"


def test_the_live_runner_is_the_runner_the_plant_runs():
    """Where the plant's home is readable, the live bytes are the pinned release's bytes."""
    if SITE_HOME is None:
        return
    live = SITE_HOME / "checks-runner" / "observer-checks.py"
    if not live.is_file():
        return
    reviewed = reviewed_runner(REPO, SITE_HOME)
    assert digest(live) == digest(reviewed), (
        "the live observer-check runner (%s) is not the runner the plant runs (%s); the "
        "checks the plant reads are decided by bytes no reviewed release carries"
        % (digest(live).hex()[:12], digest(reviewed).hex()[:12]))


def _runner(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "INVENTORY = (%r,)\nNOT_SCHEDULED = ()\n" % marker, encoding="utf-8")
    return path


def test_a_lagging_pin_supplies_the_reviewed_runner(tmp_path):
    """A runner change committed ahead of the pin is activation lag, not unreviewed bytes."""
    repo = tmp_path / "repo"
    head = _runner(repo / "checks-runner" / "observer-checks.py", "head")
    release = tmp_path / "site" / "releases" / ("a" * 40)
    pinned = _runner(release / "checks-runner" / "observer-checks.py", "pin")
    home = tmp_path / "site"
    (home / "runtime").mkdir()
    (home / "runtime" / "current").symlink_to(release)

    assert digest(head) != digest(pinned)
    assert reviewed_runner(repo, home) == pinned


def test_without_a_pin_the_head_runner_is_reviewed(tmp_path):
    """CI and a home without a pointer review HEAD's copy; they never fail for want of a pin."""
    repo = tmp_path / "repo"
    head = _runner(repo / "checks-runner" / "observer-checks.py", "head")
    assert reviewed_runner(repo, None) == head

    home = tmp_path / "site"
    (home / "runtime").mkdir(parents=True)
    assert reviewed_runner(repo, home) == head


def test_a_pin_without_a_runner_falls_back_to_head(tmp_path):
    """An old release tree cannot leave the live copy unreviewed."""
    repo = tmp_path / "repo"
    head = _runner(repo / "checks-runner" / "observer-checks.py", "head")
    release = tmp_path / "site" / "releases" / ("b" * 40)
    release.mkdir(parents=True)
    home = tmp_path / "site"
    (home / "runtime").mkdir()
    (home / "runtime" / "current").symlink_to(release)

    assert reviewed_runner(repo, home) == head
