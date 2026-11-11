import os
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


def test_rewritten_start_commit_still_observes_and_attributes_the_wakes_commit(repo):
    """A shared tip amended under the wake leaves head_start a sibling of head_end.

    Live 2026-11-11: rust-plan amended its own just-pushed commit, then genome
    reset to origin/main; two Health wakes started and ended on sibling commits
    and were recorded as spurious UNKNOWN. The commit made during the wake is still
    reachable from head_end, so it is observed and attributed, not discarded.
    """
    own = commit(repo, "own\n\nMishe-Wake: k\nMishe-Role: docs")
    start = w.begin(repo, role="docs", key="k", now=1)
    # Same tree and message; only the committer time differs, so the amend produces
    # a sibling commit exactly as the live case did.
    subprocess.check_output(["git", "-C", str(repo), "commit", "--amend", "-qm",
                             "own\n\nMishe-Wake: k\nMishe-Role: docs"],
                            text=True, env={**os.environ, "GIT_COMMITTER_DATE": "2026-11-11T00:55:35+00:00"})
    amended = git(repo, "rev-parse", "HEAD")
    assert amended != own
    result = w.finish(start, repo=repo, now=2, claimed_result="changed")
    assert result["evidence_state"] == "KNOWN"
    assert result["commits_observed"] == [amended]
    assert result["commits_attributed"] == [amended]


def test_artifacts_are_confined_and_changed_binding_is_rejected(tmp_path):
    h = tmp_path / "home"; h.mkdir(); p = h / "evidence"; p.write_text("yes")
    fact = w.artifact(h, p)
    p.write_text("changed")
    assert not w.artifact_matches(h, fact)
    with pytest.raises(ValueError): w.artifact(h, tmp_path / "outside")


def test_unchanged_blocked_wait_is_typed_from_observed_work_state(repo):
    from mishe_tauftauf import work_state
    (repo/"charters").mkdir()
    for role in ("docs","steward"): (repo/"charters"/f"{role}.md").write_text(role)
    work_state.save(repo,"docs",dict(version=1,role="docs",initiative="cause",tasks=[dict(id="cause",state="waiting",
        wait=dict(resolver="docs",escalate_to="steward",deadline=1000,evidence_needed="new bytes",action_at_deadline="decide",paths={},signals={}))]))
    start=w.begin(repo,role="docs",key="k",now=1,home=repo)
    result=w.finish(start,repo=repo,home=repo,now=2,claimed_result="blocked")
    assert result["unchanged_wait"] is True
    record=work_state.load(repo,"docs");record["tasks"].append(dict(id="edit",state="ready"));work_state.save(repo,"docs",record)
    assert w.finish(start,repo=repo,home=repo,now=3,claimed_result="blocked")["unchanged_wait"] is False


def test_staged_modifications_additions_and_deletions_are_dirty(repo):
    (repo/"file").write_text("staged change");git(repo,"add","file")
    assert "file" in w.fingerprints(repo)
    (repo/"added").write_text("staged new");git(repo,"add","added")
    assert "added" in w.fingerprints(repo)
    git(repo,"rm","-f","file")
    assert "file" in w.fingerprints(repo)
