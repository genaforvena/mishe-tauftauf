"""Live read path for the head-rewinds ``reset_origin`` basis (plate 3, step 1).

Builds a real git fixture repository (no mocks) and checks that the live reader
labels each ``reset: moving to HEAD`` reflog entry from the actual reflog and the
actual dangling stash commits. The pure helper's contract lives in
``tests/test_reset_origin_basis.py``; this file covers only the reading.

Timing: ``git stash push`` writes its own reset and the stash commit at the same
wall-clock second in the common case, but a second boundary can split them. The
stash case therefore accepts ``stash-internal`` or ``stash-correlated``; it must
never read ``unexplained`` or ``ambiguous`` for a single stash with a matching parent.
"""

import os
import subprocess

from mishe_tauftauf import discovery


def _git(repo, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout


def _init_repo(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


def test_live_stash_then_drop_reset_is_stash_labelled(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("two\n")
    _git(repo, "stash", "push", "-q", "-m", "fixture")  # writes reset: moving to HEAD
    _git(repo, "stash", "drop", "-q")                   # stash commit becomes dangling

    labels = discovery._live_reset_origins(repo)

    assert labels is not None
    assert len(labels) == 1
    assert labels[0] in {"stash-internal", "stash-correlated"}


def test_live_plain_reset_to_head_without_stash_is_unexplained(tmp_path):
    repo = _init_repo(tmp_path)
    _git(repo, "reset", "-q", "--hard", "HEAD")  # reset: moving to HEAD, no stash

    labels = discovery._live_reset_origins(repo)

    assert labels == ["unexplained"]


def test_live_reset_to_other_commit_is_not_a_head_reset(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "second")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")  # reset: moving to HEAD~1, not HEAD

    assert discovery._live_reset_origins(repo) == []


def test_live_read_outside_a_repository_is_unknown(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()

    assert discovery._live_reset_origins(not_a_repo) is None
