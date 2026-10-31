"""Reset-origin labels on the head-rewinds reading (plate 3, step 2).

Correlation-only: ``_head_rewinds`` reports how the ``reset: moving to HEAD``
entries in its reflog window were labelled, and those labels never change the
reading's ``state``. Real git fixtures, no mocks.

Prediction checked here: the reset-origin counts sum to the number of
``reset: moving to HEAD`` reflog entries in the window.
"""

import os
import subprocess

from mishe_tauftauf import discovery

LABELS = ("unexplained", "stash-internal", "stash-correlated", "ambiguous")


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
    site = repo / "site"
    site.mkdir()
    return repo, site


def _window_head_resets(repo):
    text = _git(repo, "reflog", "-n", str(discovery._HEAD_REWIND_REFLOG_LIMIT),
                "--format=%gs")
    return sum(1 for line in text.splitlines() if line == "reset: moving to HEAD")


def test_stash_reset_is_labelled_and_counted_in_the_window(tmp_path):
    repo, site = _init_repo(tmp_path)
    (repo / "a.txt").write_text("two\n")
    _git(repo, "stash", "push", "-q", "-m", "fixture")  # reset: moving to HEAD
    _git(repo, "stash", "drop", "-q")

    result = discovery._head_rewinds(site)

    origins = result["reset_origin"]
    assert origins is not None
    assert sum(origins[label] for label in LABELS) == _window_head_resets(repo) == 1
    assert origins["unexplained"] == 0
    assert "reset-origins-in-window=1" in result["sample"]


def test_plain_reset_to_head_is_unexplained_and_counted(tmp_path):
    repo, site = _init_repo(tmp_path)
    _git(repo, "reset", "-q", "--hard", "HEAD")  # reset: moving to HEAD, no stash

    result = discovery._head_rewinds(site)

    assert result["reset_origin"] == {"unexplained": 1, "stash-internal": 0,
                                      "stash-correlated": 0, "ambiguous": 0}
    assert result["state"] == "verified"


def test_reset_origin_labels_do_not_change_the_drift_state(tmp_path):
    repo, site = _init_repo(tmp_path)
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "second")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")  # drops "second": its change is absent
    _git(repo, "reset", "-q", "--hard", "HEAD")    # a HEAD reset the labels must count

    result = discovery._head_rewinds(site)

    assert result["state"] == "drift"
    assert result["absent"] == 1
    origins = result["reset_origin"]
    assert sum(origins[label] for label in LABELS) == _window_head_resets(repo) == 1
    assert origins["unexplained"] == 1
