"""Completed deliveries retire only clean, integrated, inactive candidates."""
import json
import subprocess
from pathlib import Path
import pytest
from mishe_tauftauf import delivery, retirement
from tests.test_delivery import candidate, submit, ci, git


def completed(candidate, monkeypatch):
    home, primary, work, base, head, review = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.integrate(home, "repair", "operator")
    receipt = home / "rollout.json"
    receipt.write_text(json.dumps(dict(sha=head, state="pass", consumers=["checked fixture"])))
    delivery.finish(home, "repair", "senses", receipt)
    monkeypatch.setattr(retirement, "busy", lambda *args: None)
    return home, primary, work, head


def test_retire_keeps_recoverable_bundle_and_shared_draft(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    before = git(primary, "diff", "--cached")
    row = retirement.retire(home, "repair")
    assert row["state"] == "retired"
    assert not work.exists()
    assert git(primary, "branch", "--list", "candidate") == ""
    assert git(primary, "ls-remote", "--heads", "origin", "candidate") == ""
    assert git(primary, "diff", "--cached") == before
    assert (primary / "code.txt").read_text() == "other author's draft\n"
    subprocess.run(["git", "-C", str(primary), "bundle", "verify", row["bundle"]], check=True, capture_output=True)
    assert head in git(primary, "bundle", "list-heads", row["bundle"])
    assert retirement.retire(home, "repair") == row
    delivery.check(home, "repair")  # archive records remain readable
    assert delivery.finish(home, "repair", "senses", home / "rollout.json")["phase"] == "done"


@pytest.mark.parametrize("kind", ["dirty", "busy", "branch-moved", "remote-moved"])
def test_retirement_refuses_new_work_and_active_use(candidate, monkeypatch, kind):
    home, primary, work, head = completed(candidate, monkeypatch)
    if kind == "dirty": (work / "code.txt").write_text("new draft\n")
    if kind == "busy": monkeypatch.setattr(retirement, "busy", lambda *args: "process 123 owns candidate cwd")
    if kind == "branch-moved":
        (work / "code.txt").write_text("new committed work\n")
        git(work, "commit", "-qam", "new work")
    if kind == "remote-moved":
        git(primary, "push", "-q", "origin", "main:refs/heads/candidate", "--force")
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending"
    assert work.exists()
    assert git(primary, "branch", "--list", "candidate")


def test_retirement_requires_finished_rollout(candidate, monkeypatch):
    home, primary, work, base, head, review = candidate
    submit(candidate)
    with pytest.raises(ValueError, match="completed"):
        retirement.retire(home, "repair")
    assert work.exists()


def test_retirement_resumes_after_effect_before_completion_receipt(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    original = delivery._save
    def crash(home, record):
        if record.get("retirement", {}).get("state") == "retired":
            raise RuntimeError("crash after removal")
        return original(home, record)
    monkeypatch.setattr(delivery, "_save", crash)
    with pytest.raises(RuntimeError, match="crash"):
        retirement.retire(home, "repair")
    assert not work.exists()
    monkeypatch.setattr(delivery, "_save", original)
    assert retirement.retire(home, "repair")["state"] == "retired"


def test_watcher_retries_busy_candidate_then_retires(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    monkeypatch.setattr(retirement, "busy", lambda *args: "process 123 active")
    delivery.check_all(home)
    assert "retirement=pending" in delivery.line(home)
    assert work.exists()
    monkeypatch.setattr(retirement, "busy", lambda *args: None)
    delivery.check_all(home)
    assert not work.exists()
    assert "retirement=pending" not in delivery.line(home)


def test_retirement_preserves_ignored_local_state(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    (work / ".gitignore").write_text("private-state/\n")
    git(work, "add", ".gitignore")
    # Use repository's shared exclude so candidate remains exact and clean.
    git(work, "reset", "-q", "--", ".gitignore")
    (work / ".gitignore").unlink()
    common = git(work, "rev-parse", "--path-format=absolute", "--git-common-dir")
    with (Path(common) / "info" / "exclude").open("a") as handle:
        handle.write("\nprivate-state/\n")
    private = work / "private-state"
    private.mkdir(); (private / "handoff.txt").write_text("unique ignored state")
    assert git(work, "status", "--porcelain") == ""
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending" and "ignored local state" in row["reason"]
    assert (private / "handoff.txt").read_text() == "unique ignored state"


def test_busy_detects_source_only_in_process_environment(tmp_path):
    import os, sys, time
    repo = tmp_path / "candidate"; repo.mkdir()
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repo / "src")})
    try:
        assert f"process {child.pid} environment" in retirement.busy(repo, tmp_path)
    finally:
        child.terminate(); child.wait(timeout=5)


def test_retirement_archives_ignored_bytes_arriving_after_admission(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    common = git(work, "rev-parse", "--path-format=absolute", "--git-common-dir")
    with (Path(common) / "info" / "exclude").open("a") as handle:
        handle.write("\nprivate-state/\n")
    original = Path.rename
    def add_then_rename(path, destination):
        if path == work:
            private = work / "private-state"; private.mkdir()
            (private / "unique.txt").write_text("unique concurrent bytes")
        return original(path, destination)
    monkeypatch.setattr(Path, "rename", add_then_rename)
    row = retirement.retire(home, "repair")
    assert row["state"] == "retired"
    assert (Path(row["tree_archive"]) / "private-state" / "unique.txt").read_text() == "unique concurrent bytes"
    assert Path(row["admin_archive"]).is_dir()
    assert str(work) not in git(primary, "worktree", "list", "--porcelain")


def test_retirement_fetches_main_advanced_by_separate_clone(candidate, monkeypatch, tmp_path):
    home, primary, work, head = completed(candidate, monkeypatch)
    other = tmp_path / "other-clone"
    subprocess.run(["git", "clone", "-q", "-b", "main", git(primary, "remote", "get-url", "origin"), str(other)], check=True)
    git(other, "config", "user.email", "test@example.invalid"); git(other, "config", "user.name", "Test")
    (other / "later.txt").write_text("later main work")
    git(other, "add", "."); git(other, "commit", "-qm", "later main"); git(other, "push", "-q", "origin", "main")
    assert retirement.retire(home, "repair")["state"] == "retired"


def test_retirement_respects_explicit_worktree_reservation(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    git(primary, "worktree", "lock", "--reason", "Active reserved worktree", str(work))
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending" and "locked" in row["reason"]
    assert work.exists() and str(work) in git(primary, "worktree", "list", "--porcelain")
