import subprocess
from datetime import datetime, timedelta, timezone

from mishe_tauftauf import landing_debt as debt


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    (root / "draft.txt").write_text("base")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def test_unclaimed_dirty_is_red_and_age_survives_edits(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    start = datetime(2026, 9, 30, tzinfo=timezone.utc)
    (root / "draft.txt").write_text("draft")
    first = debt.audit(home, root, now=start)
    assert first["state"] == "RED"
    assert first["paths"][0]["reason"] == "unclaimed"
    (root / "draft.txt").write_text("changed")
    later = debt.audit(home, root, now=start + timedelta(hours=25))
    assert later["paths"][0]["first_seen"] == first["paths"][0]["first_seen"]
    assert later["paths"][0]["stale"]


def test_claim_is_exact_and_expired_claim_cannot_hide_changed_bytes(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("draft")
    debt.claim(home, root, "delivery", "genome", "prepare review", ["draft.txt"])
    assert debt.audit(home, root)["state"] == "AMBER"
    (root / "draft.txt").write_text("other writer")
    report = debt.audit(home, root)
    assert report["state"] == "RED"
    assert report["paths"][0]["reason"] == "claim bytes changed"


def test_already_landed_draft_remains_debt_until_reconciled(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    base = git(root, "rev-parse", "HEAD")
    (root / "draft.txt").write_text("published")
    git(root, "commit", "-qam", "published")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "reset", "--mixed", base)
    report = debt.audit(home, root)
    assert report["state"] == "RED"
    assert report["behind"] == 1
    assert report["paths"][0]["published"]
    assert "already on origin" in list(debt.lines(report))[1]


def test_staging_change_invalidates_claim_even_when_worktree_identical(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("draft")
    debt.claim(home, root, "delivery", "genome", "review", ["draft.txt"])
    git(root, "add", "draft.txt")
    assert debt.audit(home, root)["paths"][0]["reason"] == "claim bytes changed"


def test_spaces_deletions_and_clean_retire_observation(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "a space.txt").write_text("new")
    (root / "draft.txt").unlink()
    report = debt.audit(home, root)
    assert {p["path"] for p in report["paths"]} == {"a space.txt", "draft.txt"}
    (root / "a space.txt").unlink()
    git(root, "restore", "draft.txt")
    assert debt.audit(home, root)["state"] == "GREEN"


def test_closed_task_claim_is_debt_and_missing_origin_is_unknown(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("draft")
    debt.claim(home, root, "delivery", "genome", "review", ["draft.txt"])
    assert debt.audit(home, root, open_tasks=set())["paths"][0]["reason"] == "task closed or absent"
    git(root, "update-ref", "-d", "refs/remotes/origin/main")
    assert debt.audit(home, root)["state"] == "UNKNOWN"


def test_outside_edits_create_one_prioritized_actionable_intake(tmp_path):
    from mishe_tauftauf.feed import Feed
    from mishe_tauftauf import task_state, landing
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("outside edit")
    report = debt.audit(home, root)
    identity = debt.intake(home, report)
    entries = Feed(home).entries()
    selected = task_state.select_task(entries, "genome")
    assert selected.identity == identity
    assert "assess whether" in selected.next_step
    assert landing.queue(entries)[0].identity == identity
    before = len(entries)
    assert debt.intake(home, debt.audit(home, root)) == identity
    assert len(Feed(home).entries()) == before
    # A consumed unchanged intake is never rearmed by another audit.
    task_state.record_attempt(home, selected, 99, 1)
    debt.intake(home, debt.audit(home, root))
    assert task_state.select_task(Feed(home).entries(), "genome") is None


def test_closed_intake_with_unresolved_bytes_reopens_instead_of_rotting(tmp_path):
    from mishe_tauftauf import task_state
    from mishe_tauftauf.feed import Feed
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("outside edit")
    identity = debt.intake(home, debt.audit(home, root))
    proof = tmp_path / "proof.md"
    proof.write_text("prematurely closed")
    task_state.finish(home, identity, "genome", "closed", proof)
    debt.intake(home, debt.audit(home, root))
    assert task_state.select_task(Feed(home).entries(), "genome").identity == identity


def test_published_symlink_and_executable_modes_are_exact(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "link").symlink_to("draft.txt")
    git(root, "add", "link")
    git(root, "commit", "-qm", "link")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "reset", "--mixed", "HEAD~1")
    assert next(r for r in debt.audit(home, root)["paths"] if r["path"] == "link")["published"]
    (root / "draft.txt").chmod(0o755)
    assert not next(r for r in debt.audit(home, root)["paths"] if r["path"] == "draft.txt")["published"]


def test_audit_preserves_index_bytes_and_excludes_plant_state(tmp_path):
    root = repo(tmp_path)
    home = root / "site"
    home.mkdir()
    (home / "chat.log").write_text("runtime")
    (root / "draft.txt").write_text("outside")
    before = (root / ".git/index").read_bytes()
    report = debt.audit(home, root)
    assert (root / ".git/index").read_bytes() == before
    assert [r["path"] for r in report["paths"]] == ["draft.txt"]


def test_staged_deletion_cannot_be_called_published_when_worktree_matches_origin(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    git(root, "rm", "--cached", "draft.txt")
    assert not debt.audit(home, root)["paths"][0]["published"]


def test_staged_executable_mode_is_not_already_published(tmp_path):
    root = repo(tmp_path)
    home = tmp_path / "site"
    git(root, "update-index", "--chmod=+x", "draft.txt")
    assert not debt.audit(home, root)["paths"][0]["published"]


def test_changed_outside_bytes_make_waiting_intake_actionable(tmp_path):
    from mishe_tauftauf import task_state
    from mishe_tauftauf.feed import Feed
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("outside edit")
    identity = debt.intake(home, debt.audit(home, root))
    task_state.record_attempt(home, task_state.select_task(Feed(home).entries(), "genome"), 99, 1)
    (root / "draft.txt").write_text("new edit")
    debt.intake(home, debt.audit(home, root))
    assert task_state.select_task(Feed(home).entries(), "genome").identity == identity


def test_closed_claim_can_be_adopted_but_live_claim_conflicts(tmp_path):
    import pytest
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("outside")
    debt.claim(home, root, "old", "senses", "review", ["draft.txt"])
    with pytest.raises(ValueError, match="claim conflict"):
        debt.claim(home, root, "new", "genome", "adopt", ["draft.txt"], open_tasks={"old", "new"})
    debt.claim(home, root, "new", "genome", "adopt", ["draft.txt"], open_tasks={"new"})
    assert debt.audit(home, root)["paths"][0]["owner"] == "genome"


def test_interrupted_registration_recovers_without_duplicate_task(tmp_path, monkeypatch):
    from mishe_tauftauf import landing, task_state
    from mishe_tauftauf.feed import Feed
    import pytest
    root = repo(tmp_path)
    home = tmp_path / "site"
    (root / "draft.txt").write_text("outside")
    register = landing.register
    def fail(*args, **kwargs):
        raise OSError("interrupted registration")
    monkeypatch.setattr(landing, "register", fail)
    with pytest.raises(OSError):
        debt.intake(home, debt.audit(home, root))
    monkeypatch.setattr(landing, "register", register)
    identity = debt.intake(home, debt.audit(home, root))
    assert landing.queue(Feed(home).entries())[0].identity == identity
    assert len(task_state.states(Feed(home).entries())) == 1
