import subprocess
from pathlib import Path
import pytest
from mishe_tauftauf import work_receipts as w


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "test")
    git(tmp_path, "config", "user.email", "test@example.org")
    (tmp_path / "file").write_text("base")
    git(tmp_path, "add", "."); git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def commit(repo, text):
    (repo / "file").write_text(text)
    git(repo, "add", "."); git(repo, "commit", "-qm", text)
    return git(repo, "rev-parse", "HEAD")


def test_no_commit_and_wall_only_changed_is_bookkeeping(repo):
    start = w.begin(repo, role="docs", key="k", now=1)
    result = w.finish(start, repo=repo, now=2, claimed_result="changed")
    assert result["commits_observed"] == []
    assert result["classification"] == "bookkeeping"


def test_exact_trailers_attribute_only_own_commit(repo):
    start = w.begin(repo, role="docs", key="k", now=1)
    human = commit(repo, "human")
    own = commit(repo, "own\n\nMishe-Wake: k\nMishe-Role: docs")
    result = w.finish(start, repo=repo, now=2, claimed_result="changed")
    assert result["commits_observed"] == [human, own]
    assert result["commits_attributed"] == [own]


def test_rewind_and_missing_git_are_unknown(repo):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "new")
    start = w.begin(repo, role="docs", key="k", now=1)
    git(repo, "reset", "--hard", base)
    assert w.finish(start, repo=repo, now=2)["evidence_state"] == "UNKNOWN"
    assert w.begin(None, role="docs", key="k", now=1)["evidence_state"] == "UNKNOWN"


def test_artifacts_are_confined_and_changed_binding_is_rejected(tmp_path):
    h = tmp_path / "home"; h.mkdir(); p = h / "evidence"; p.write_text("yes")
    fact = w.artifact(h, p)
    p.write_text("changed")
    assert not w.artifact_matches(h, fact)
    with pytest.raises(ValueError): w.artifact(h, tmp_path / "outside")
