"""Fixtures for ``sense.repo.push-freshness`` against real throwaway Git repositories.

The reading names commits that do not reach origin's ``main`` promptly. Each
fixture builds a bare origin and a checkout in a temporary directory, so the
verdicts come from Git itself rather than from a mocked history.
"""

import os
import subprocess
from pathlib import Path

from mishe_tauftauf import discovery

NOW = 1_800_000_000


def _git(cwd: Path, *args: str, when: int | None = None) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f"{when} +0000", GIT_COMMITTER_DATE=f"{when} +0000")
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _commit(repo: Path, name: str, when: int) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name, when=when)


def _checkout(tmp_path: Path, pushed_at: int) -> tuple[Path, Path]:
    """Return a checkout whose ``origin/main`` was pushed at ``pushed_at``."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q", "-b", "main")
    (checkout / ".mishe-tauftauf").mkdir()
    _git(checkout, "remote", "add", "origin", str(origin))
    _commit(checkout, "base", pushed_at)
    _git(checkout, "push", "-q", "origin", "main")
    return checkout, checkout / ".mishe-tauftauf"


def test_fully_pushed_checkout_reads_verified(tmp_path):
    checkout, home = _checkout(tmp_path, NOW - 3600)
    reading = discovery._repo_push_freshness(home, now=NOW)
    assert reading["state"] == "verified"
    assert reading["unpushed"] == 0


def test_commits_waiting_past_the_lag_bound_read_drift(tmp_path):
    checkout, home = _checkout(tmp_path, NOW - 5 * 86400)
    _commit(checkout, "waiting", NOW - discovery.PUSH_LAG_S - 60)
    reading = discovery._repo_push_freshness(home, now=NOW)
    assert reading["state"] == "drift"
    assert reading["unpushed"] == 1
    assert reading["lagging"] == 1


def test_young_unpushed_commits_are_verified_and_counted(tmp_path):
    checkout, home = _checkout(tmp_path, NOW - 3600)
    _commit(checkout, "fresh", NOW - 600)
    reading = discovery._repo_push_freshness(home, now=NOW)
    assert reading["state"] == "verified"
    assert reading["unpushed"] == 1


def test_unreadable_origin_is_unknown_not_healthy(tmp_path):
    checkout, home = _checkout(tmp_path, NOW - 3600)
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    reading = discovery._repo_push_freshness(home, now=NOW)
    assert reading["state"] == "unknown"
    assert "origin-unreadable" in reading["sample"]


def test_missing_checkout_gitdir_is_unknown(tmp_path):
    home = tmp_path / ".mishe-tauftauf"
    home.mkdir()
    assert discovery._repo_push_freshness(home, now=NOW)["state"] == "unknown"
