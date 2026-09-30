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


@pytest.mark.parametrize("keep_local_ref", [True, False])
def test_retirement_supports_detached_candidate_and_main_only_refs(candidate, monkeypatch, tmp_path, keep_local_ref):
    home, primary, work, head = completed(candidate, monkeypatch)
    git(work, "checkout", "-q", "--detach", head)
    if not keep_local_ref:
        git(primary, "update-ref", "-d", "refs/heads/candidate", head)
    row = retirement.retire(home, "repair")
    assert row["state"] == "retired", row
    assert git(primary, "for-each-ref", "--format=%(refname)", "refs/heads") == "refs/heads/main"
    assert git(primary, "ls-remote", "--heads", "origin").split()[1:] == ["refs/heads/main"]
    assert not git(primary, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/candidate")
    recovered = tmp_path / "standalone-recovery.git"
    git(tmp_path, "init", "--bare", "-q", str(recovered))
    subprocess.run(["git", "-C", str(recovered), "bundle", "verify", row["bundle"]], check=True, capture_output=True)
    git(recovered, "fetch", "-q", row["bundle"], head)
    assert git(recovered, "rev-parse", "FETCH_HEAD") == head
    assert (Path(row["admin_archive"]) / "index").is_file()
    assert retirement.retire(home, "repair") == row


def test_retirement_removes_exact_stale_tracking_after_independent_remote_delete(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    git(primary, "fetch", "-q", "origin", "candidate:refs/remotes/origin/candidate")
    origin = Path(git(primary, "remote", "get-url", "origin"))
    git(origin, "update-ref", "-d", "refs/heads/candidate", head)
    assert git(primary, "rev-parse", "refs/remotes/origin/candidate") == head
    row = retirement.retire(home, "repair")
    assert row["state"] == "retired", row
    assert not git(primary, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/candidate")


def test_retirement_preserves_advanced_tracking_ref(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    original = git(primary, "rev-parse", "HEAD")
    git(primary, "update-ref", "refs/remotes/origin/candidate", original)
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending", row
    assert "tracking" in row["reason"]
    assert work.exists()
    assert git(primary, "rev-parse", "refs/remotes/origin/candidate") == original
    assert git(primary, "ls-remote", "--heads", "origin", "candidate").split()[0] == head


def test_tracking_advance_during_remote_delete_is_preserved(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    git(primary, "fetch", "-q", "origin", "candidate:refs/remotes/origin/candidate")
    advanced = git(primary, "rev-parse", "HEAD")
    original = delivery._git
    changed = []
    def race(repo, *args):
        if args and args[0] == "push" and ":refs/heads/candidate" in args:
            git(primary, "update-ref", "refs/remotes/origin/candidate", advanced)
            changed.append(True)
        return original(repo, *args)
    monkeypatch.setattr(delivery, "_git", race)
    row = retirement.retire(home, "repair")
    assert changed
    assert row["state"] == "pending", row
    assert "tracking" in row["reason"]
    assert work.exists()
    assert git(primary, "rev-parse", "refs/remotes/origin/candidate") == advanced
    assert Path(row["bundle"]).is_file()


@pytest.mark.parametrize("same_head", [False, True])
def test_local_ref_appearing_during_detached_retirement_is_preserved(candidate, monkeypatch, same_head):
    home, primary, work, head = completed(candidate, monkeypatch)
    git(work, "checkout", "-q", "--detach", head)
    git(primary, "update-ref", "-d", "refs/heads/candidate", head)
    created = head if same_head else git(primary, "rev-parse", "HEAD")
    original = delivery._git
    def race(repo, *args):
        if args and args[0] == "push" and ":refs/heads/candidate" in args:
            git(primary, "update-ref", "refs/heads/candidate", created)
        return original(repo, *args)
    monkeypatch.setattr(delivery, "_git", race)
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending", row
    assert "local" in row["reason"]
    assert work.exists()
    assert git(primary, "rev-parse", "refs/heads/candidate") == created
    assert Path(row["bundle"]).is_file()


def test_remote_ref_recreated_after_deletion_prevents_retired_receipt(candidate, monkeypatch):
    home, primary, work, head = completed(candidate, monkeypatch)
    origin = Path(git(primary, "remote", "get-url", "origin"))
    original = delivery._git
    def race(repo, *args):
        result = original(repo, *args)
        if args and args[0] == "push" and ":refs/heads/candidate" in args:
            git(origin, "update-ref", "refs/heads/candidate", head)
        return result
    monkeypatch.setattr(delivery, "_git", race)
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending", row
    assert "remote" in row["reason"]
    assert git(primary, "ls-remote", "--heads", "origin", "candidate").split()[0] == head
    assert Path(row["bundle"]).is_file()


@pytest.mark.parametrize("kind", ["local", "remote", "tracking"])
def test_ref_recreated_after_last_deletion_prevents_retired_receipt(candidate, monkeypatch, kind):
    home, primary, work, head = completed(candidate, monkeypatch)
    origin = Path(git(primary, "remote", "get-url", "origin"))
    original = delivery._git
    def race(repo, *args):
        result = original(repo, *args)
        if args[:3] == ("update-ref", "-d", "refs/heads/candidate"):
            target = origin if kind == "remote" else primary
            ref = "refs/remotes/origin/candidate" if kind == "tracking" else "refs/heads/candidate"
            git(target, "update-ref", ref, head)
        return result
    monkeypatch.setattr(delivery, "_git", race)
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending", row
    assert kind in row["reason"]
    assert Path(row["tree_archive"]).is_dir()
    assert Path(row["bundle"]).is_file()
    monkeypatch.setattr(delivery, "_git", original)
    assert retirement.retire(home, "repair")["state"] == "pending"
    target = origin if kind == "remote" else primary
    ref = "refs/remotes/origin/candidate" if kind == "tracking" else "refs/heads/candidate"
    assert git(target, "rev-parse", ref) == head


@pytest.mark.parametrize("kind", ["local", "remote", "tracking"])
def test_retired_receipt_does_not_hide_or_delete_recreated_ref(candidate, monkeypatch, kind):
    home, primary, work, head = completed(candidate, monkeypatch)
    row = retirement.retire(home, "repair")
    assert row["state"] == "retired"
    origin = Path(git(primary, "remote", "get-url", "origin"))
    target = origin if kind == "remote" else primary
    ref = "refs/remotes/origin/candidate" if kind == "tracking" else "refs/heads/candidate"
    git(target, "update-ref", ref, head)
    retry = retirement.retire(home, "repair")
    assert retry["state"] == "pending", retry
    assert kind in retry["reason"]
    assert git(target, "rev-parse", ref) == head
    assert retirement.retire(home, "repair")["state"] == "pending"
    assert git(target, "rev-parse", ref) == head


@pytest.mark.parametrize("after_effect", [False, True])
def test_crash_at_remote_deletion_boundary_never_replays_uncertain_refs(candidate, monkeypatch, after_effect):
    home, primary, work, head = completed(candidate, monkeypatch)
    origin = Path(git(primary, "remote", "get-url", "origin"))
    original = delivery._git
    def crash(repo, *args):
        if args and args[0] == "push" and ":refs/heads/candidate" in args:
            if after_effect:
                original(repo, *args)
            raise RuntimeError("uncertain remote deletion")
        return original(repo, *args)
    monkeypatch.setattr(delivery, "_git", crash)
    with pytest.raises(RuntimeError, match="uncertain remote"):
        retirement.retire(home, "repair")
    monkeypatch.setattr(delivery, "_git", original)
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending"
    assert work.exists()
    assert git(primary, "rev-parse", "refs/heads/candidate") == head
    assert bool(git(origin, "for-each-ref", "--format=%(objectname)", "refs/heads/candidate")) != after_effect
    # After explicit owner reconciliation, recover archive work without another push.
    git(work, "checkout", "-q", "--detach", head)
    git(primary, "update-ref", "-d", "refs/heads/candidate", head)
    git(primary, "update-ref", "-d", "refs/remotes/origin/candidate")
    git(origin, "update-ref", "-d", "refs/heads/candidate")
    def no_push(repo, *args):
        assert args[0] != "push"
        return original(repo, *args)
    monkeypatch.setattr(delivery, "_git", no_push)
    assert retirement.retire(home, "repair")["state"] == "retired"


@pytest.mark.parametrize("kind", ["local", "remote", "tracking"])
def test_ref_recreated_during_archive_recovery_is_never_deleted(candidate, monkeypatch, kind):
    home, primary, work, head = completed(candidate, monkeypatch)
    origin = Path(git(primary, "remote", "get-url", "origin"))
    original = delivery._git
    def crash(repo, *args):
        if args and args[0] == "push" and ":refs/heads/candidate" in args:
            raise RuntimeError("before first ref effect")
        return original(repo, *args)
    monkeypatch.setattr(delivery, "_git", crash)
    with pytest.raises(RuntimeError):
        retirement.retire(home, "repair")
    git(work, "checkout", "-q", "--detach", head)
    git(primary, "update-ref", "-d", "refs/heads/candidate", head)
    git(primary, "update-ref", "-d", "refs/remotes/origin/candidate")
    git(origin, "update-ref", "-d", "refs/heads/candidate")
    target = origin if kind == "remote" else primary
    ref = "refs/remotes/origin/candidate" if kind == "tracking" else "refs/heads/candidate"
    def race(repo, *args):
        result = original(repo, *args)
        if args and args[0] == "fetch":
            git(target, "update-ref", ref, head)
        return result
    monkeypatch.setattr(delivery, "_git", race)
    row = retirement.retire(home, "repair")
    assert row["state"] == "pending", row
    assert git(target, "rev-parse", ref) == head
    assert work.exists()
