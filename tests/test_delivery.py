"""Delivery acceptance tests use separate real worktrees and a bare origin."""
import json
import subprocess
from unittest.mock import patch

import pytest

from mishe_tauftauf import delivery, task_state
from mishe_tauftauf.feed import Feed


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   stderr=subprocess.PIPE).strip()


@pytest.fixture
def candidate(tmp_path):
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    primary = tmp_path / "primary"
    primary.mkdir()
    git(primary, "init", "-q", "-b", "main")
    git(primary, "config", "user.email", "test@example.invalid")
    git(primary, "config", "user.name", "Test")
    (primary / "code.txt").write_text("base\n")
    git(primary, "add", ".")
    git(primary, "commit", "-qm", "base")
    git(primary, "remote", "add", "origin", str(remote))
    git(primary, "push", "-q", "origin", "main")
    base = git(primary, "rev-parse", "HEAD")
    work = tmp_path / "candidate"
    git(primary, "worktree", "add", "-q", "-b", "candidate", str(work), base)
    (work / "code.txt").write_text("candidate\n")
    git(work, "commit", "-qam", "candidate")
    head = git(work, "rev-parse", "HEAD")
    git(work, "push", "-q", "origin", "candidate")
    # Shared draft ownership and index state are irrelevant to isolated integration.
    (primary / "code.txt").write_text("other author's draft\n")
    git(primary, "add", "code.txt")
    home = primary / "site"
    from mishe_tauftauf.cli import initialize
    initialize(home)
    with (primary / ".git/info/exclude").open("a") as handle:
        handle.write("\n/site/\n")
    review = tmp_path / "review.json"
    review.write_text(json.dumps(dict(base=base, head=head, reviewer="witness", verdict="pass")))
    return home, primary, work, base, head, review


def submit(candidate):
    home, _, work, base, _, review = candidate
    return delivery.submit(home, "repair", "senses", work, base, "candidate", review)


def ci(head, state="pass", run="42"):
    return dict(sha=head, state=state, run=run, detail=state, url="https://example.test/42")


def test_pending_ci_has_no_genome_task_and_pass_dispatches_once(candidate, monkeypatch):
    home, _, _, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "pending"))
    delivery.check(home, "repair")
    assert task_state.select_task(Feed(home).entries(), "genome") is None
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    selected = task_state.select_task(Feed(home).entries(), "genome")
    assert selected.delivery == "repair"
    task_state.record_attempt(home, selected, 99, 1)
    before = len(Feed(home).entries())
    delivery.check(home, "repair")
    assert len(Feed(home).entries()) == before
    assert task_state.select_task(Feed(home).entries(), "genome") is None
    # Changing prose cannot rearm a fact-owned integration attempt.
    with pytest.raises(ValueError, match="delivery check"):
        task_state.set_step(home, selected.identity, "genome", "try again", "new wording", candidate[-1])


def test_integration_preserves_primary_draft_and_returns_delivery_to_author(candidate, monkeypatch):
    home, primary, work, base, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    before = git(primary, "diff", "--cached")
    delivery.integrate(home, "repair", "genome")
    assert git(primary, "ls-remote", "origin", "refs/heads/main").split()[0] == head
    assert git(primary, "rev-parse", "HEAD") == base
    assert git(primary, "diff", "--cached") == before
    assert (primary / "code.txt").read_text() == "other author's draft\n"
    assert git(work, "status", "--porcelain") == ""
    record = delivery.load(home, "repair")
    assert record["owner"] == "senses" and record["phase"] == "integrated"
    assert task_state.select_task(Feed(home).entries(), "genome") is None
    assert any(e.body.startswith("[task-event] delivery-repair-integrated") for e in Feed(home).entries())


def test_review_is_independent_exact_and_immutable(candidate, monkeypatch):
    home, _, _, base, head, review = candidate
    review.write_text(json.dumps(dict(base=base, head=head, reviewer="senses", verdict="pass")))
    with pytest.raises(ValueError, match="independent"):
        submit(candidate)
    review.write_text(json.dumps(dict(base=base, head="a" * 40, reviewer="witness", verdict="pass")))
    with pytest.raises(ValueError, match="exact"):
        submit(candidate)
    review.write_text(json.dumps(dict(base=base, head=head, reviewer="witness", verdict="pass")))
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    review.write_text(review.read_text() + "\n")
    with pytest.raises(ValueError, match="review"):
        delivery.integrate(home, "repair", "genome")
    delivery.check(home, "repair")
    assert task_state.select_task(Feed(home).entries(), "genome") is None


def test_unpublished_or_dirty_candidate_is_not_admitted(candidate):
    _, _, work, _, _, _ = candidate
    git(work, "push", "-q", "origin", "--delete", "candidate")
    with pytest.raises(ValueError, match="published"):
        submit(candidate)
    git(work, "push", "-q", "origin", "candidate")
    (work / "code.txt").write_text("uncommitted")
    with pytest.raises(ValueError, match="clean"):
        submit(candidate)


def test_advanced_main_blocks_only_affected_candidate(candidate, monkeypatch):
    home, primary, _, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    other = primary.parent / "other"
    git(primary, "worktree", "add", "-q", "-b", "other", str(other), "main")
    (other / "other.txt").write_text("other change")
    git(other, "add", ".")
    git(other, "commit", "-qm", "other")
    git(other, "push", "-q", "origin", "HEAD:main")
    delivery.check(home, "repair")
    assert delivery.load(home, "repair")["phase"] == "blocked"
    Feed(home).append("operator", "[task] unrelated owner=genome acceptance=repair")
    assert task_state.select_task(Feed(home).entries(), "genome").identity == "unrelated"
    with pytest.raises(ValueError, match="main"):
        delivery.integrate(home, "repair", "genome")
    assert git(primary, "ls-remote", "origin", "refs/heads/main").split()[0] != head


def test_push_without_receipt_is_recovered_without_second_push(candidate, monkeypatch):
    home, _, work, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    git(work, "push", "-q", "origin", "HEAD:main")
    # Crash occurred after remote side effect. Reconcile actual main before retrying.
    delivery.check(home, "repair")
    assert delivery.load(home, "repair")["phase"] == "integrated"
    assert task_state.select_task(Feed(home).entries(), "genome") is None


def test_final_ci_and_rollout_are_authors_obligation(candidate, monkeypatch):
    home, _, _, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    delivery.integrate(home, "repair", "operator")
    proof = home / "rollout.json"
    proof.write_text(json.dumps(dict(sha=head, state="pass", consumers=["owned-site"])))
    with pytest.raises(ValueError, match="owner"):
        delivery.finish(home, "repair", "genome", proof)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "pending"))
    with pytest.raises(ValueError, match="CI"):
        delivery.finish(home, "repair", "senses", proof)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.finish(home, "repair", "senses", proof)
    assert delivery.load(home, "repair")["phase"] == "done"


def test_author_waits_for_real_transition_and_repair_wakes_author(candidate, monkeypatch):
    home, _, _, _, head, _ = candidate
    submit(candidate)
    assert task_state.select_task(Feed(home).entries(), "senses") is None
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "fail"))
    delivery.check(home, "repair")
    assert task_state.select_task(Feed(home).entries(), "senses").identity == "repair"
    selected = task_state.select_task(Feed(home).entries(), "senses")
    task_state.record_attempt(home, selected, 99, 1)
    delivery.check(home, "repair")
    assert task_state.select_task(Feed(home).entries(), "senses") is None


def test_superseding_revision_cannot_replace_active_integration(candidate, monkeypatch):
    home, _, work, base, head, review = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    selected = task_state.select_task(Feed(home).entries(), "genome")
    wake = Feed(home).append("seed", f"seed wake genome observation=1 task={selected.identity}")
    task_state.record_attempt(home, selected, wake.sequence, 1)
    (work / "code.txt").write_text("new candidate\n")
    git(work, "commit", "-qam", "new revision")
    new_head = git(work, "rev-parse", "HEAD")
    git(work, "push", "-q", "origin", "candidate")
    review.write_text(json.dumps(dict(base=base, head=new_head, reviewer="witness", verdict="pass")))
    with pytest.raises(ValueError, match="active"):
        submit(candidate)
    assert delivery.load(home, "repair")["head"] == head


def test_crash_after_submit_save_recovers_author_task(candidate):
    home, _, _, _, head, _ = candidate
    with patch.object(delivery, "_wait_owner", side_effect=RuntimeError("crash")):
        with pytest.raises(RuntimeError):
            submit(candidate)
    submit(candidate)
    assert task_state.registry(Feed(home).entries())["repair"].owner == "senses"


def test_crash_after_integrated_save_recovers_author_wake(candidate):
    home, _, _, _, head, _ = candidate
    with patch.object(delivery, "read_ci", return_value=ci(head)):
        submit(candidate)
        delivery.check(home, "repair")
        with patch.object(delivery, "_sync", side_effect=RuntimeError("crash")):
            with pytest.raises(RuntimeError):
                delivery.integrate(home, "repair", "genome")
        delivery.check(home, "repair")
        assert delivery.load(home, "repair")["phase"] == "integrated"
        assert task_state.select_task(Feed(home).entries(), "senses").identity == "repair"
        before = len(Feed(home).entries())
        delivery.check(home, "repair")
        assert len(Feed(home).entries()) == before


def test_crash_after_ready_save_recovers_integration_without_repeat(candidate):
    home, _, _, _, head, _ = candidate
    with patch.object(delivery, "read_ci", return_value=ci(head)):
        submit(candidate)
        delivery.check(home, "repair")
        with patch.object(delivery, "read_ci", return_value=ci(head, "pending")):
            delivery.check(home, "repair")
        with patch.object(delivery, "_sync", side_effect=RuntimeError("crash")):
            with pytest.raises(RuntimeError):
                delivery.check(home, "repair")
        delivery.check(home, "repair")
        selected = task_state.select_task(Feed(home).entries(), "genome")
        assert selected is not None and selected.delivery == "repair"
        task_state.record_attempt(home, selected, 99, 1)
        delivery.check(home, "repair")
        assert task_state.select_task(Feed(home).entries(), "genome") is None


def test_crash_after_finish_save_recovers_author_closure(candidate):
    home, _, _, _, head, _ = candidate
    with patch.object(delivery, "read_ci", return_value=ci(head)):
        submit(candidate)
        delivery.check(home, "repair")
        delivery.integrate(home, "repair", "genome")
        proof = home / "rollout.json"
        proof.write_text(json.dumps(dict(sha=head, state="pass", consumers=["site"])))
        with patch.object(delivery, "_sync", side_effect=RuntimeError("crash")):
            with pytest.raises(RuntimeError):
                delivery.finish(home, "repair", "senses", proof)
        delivery.check(home, "repair")
        assert task_state.registry(Feed(home).entries())["repair"].status == "done"


@pytest.mark.parametrize("payload", [[], None, {}, {"version": 1, "identity": "bad"}])
def test_malformed_candidate_is_unknown_without_stopping_ci_watcher(tmp_path, payload):
    home = tmp_path / "site"
    (home / "deliveries").mkdir(parents=True)
    (home / "deliveries/bad.json").write_text(json.dumps(payload))
    delivery.check_all(home)
    assert "UNKNOWN bad.json" in delivery.line(home)


def test_candidate_from_other_repository_cannot_be_integrated(candidate, tmp_path):
    home, _, work, base, _, review = candidate
    other = tmp_path / "unrelated"
    other.mkdir()
    git(other, "init", "-q")
    with pytest.raises(ValueError, match="canonical"):
        delivery.submit(other / "site", "repair", "senses", work, base, "candidate", review)


def test_final_ci_transition_wakes_waiting_author_once(candidate, monkeypatch):
    home, _, _, _, head, review = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    delivery.integrate(home, "repair", "genome")
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head, "pending"))
    delivery.check(home, "repair")
    task_state.wait_for(home, "repair", "senses", "deploy after final CI", "main CI pending", review,
                        retry_event="delivery-repair-updated")
    assert task_state.select_task(Feed(home).entries(), "senses") is None
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    assert task_state.select_task(Feed(home).entries(), "senses").identity == "repair"
    before = len(Feed(home).entries())
    delivery.check(home, "repair")
    assert len(Feed(home).entries()) == before


def test_fact_owned_integration_cannot_be_offered_waited_or_finished_with_prose(candidate, monkeypatch):
    home, _, _, _, head, review = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    identity = task_state.select_task(Feed(home).entries(), "genome").identity
    with pytest.raises(ValueError, match="delivery"):
        task_state.wait_for(home, identity, "genome", "retry", "new prose", review, retry_event="made-up")
    with pytest.raises(ValueError, match="delivery"):
        task_state.offer(home, identity, "genome", ["health"], review)
    with pytest.raises(ValueError, match="delivery"):
        task_state.finish(home, identity, "genome", "claimed integration", review)


def test_final_ci_failure_routes_repair_to_source_author(candidate, monkeypatch):
    from mishe_tauftauf import ci_watch
    home, _, _, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.integrate(home, "repair", "genome")
    monkeypatch.setattr(ci_watch, "read", lambda *args: ci(head, "fail"))
    ci_watch.tick(home)
    assert task_state.registry(Feed(home).entries())["ci-" + head[:12]].owner == "senses"


def test_changed_origin_cannot_supply_final_ci(candidate, monkeypatch, tmp_path):
    home, _, work, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.integrate(home, "repair", "genome")
    git(work, "remote", "set-url", "origin", str(tmp_path / "another.git"))
    proof = home / "rollout.json"
    proof.write_text(json.dumps(dict(sha=head, state="pass", consumers=["site"])))
    with pytest.raises(ValueError, match="origin"):
        delivery.finish(home, "repair", "senses", proof)


def test_push_failure_resumes_exact_operation_without_model_retries(candidate, monkeypatch):
    home, _, _, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    selected = task_state.select_task(Feed(home).entries(), "genome")
    task_state.record_attempt(home, selected, 99, 1)
    command = delivery._git
    def unavailable(repo, *args):
        if args[0] == "push":
            raise ValueError("push connection temporarily unavailable")
        return command(repo, *args)
    monkeypatch.setattr(delivery, "_git", unavailable)
    with pytest.raises(ValueError, match="temporarily unavailable"):
        delivery.integrate(home, "repair", "genome")
    assert delivery.load(home, "repair")["phase"] == "blocked"
    assert task_state.select_task(Feed(home).entries(), "senses").identity == "repair"
    before = len(Feed(home).entries())
    delivery.check(home, "repair")
    delivery.check(home, "repair")
    assert task_state.select_task(Feed(home).entries(), "genome") is None
    assert len(Feed(home).entries()) == before
    monkeypatch.setattr(delivery, "_git", command)
    delivery.check(home, "repair")
    assert task_state.select_task(Feed(home).entries(), "genome") is None
    assert delivery.load(home, "repair")["phase"] == "integrated"
    before = len(Feed(home).entries())
    delivery.check(home, "repair")
    assert len(Feed(home).entries()) == before


def test_server_hook_rejection_stays_blocked_without_rearming_models(candidate, monkeypatch):
    home, _, work, _, head, _ = candidate
    submit(candidate)
    monkeypatch.setattr(delivery, "read_ci", lambda *args: ci(head))
    delivery.check(home, "repair")
    selected = task_state.select_task(Feed(home).entries(), "genome")
    task_state.record_attempt(home, selected, 99, 1)
    hook = work.parent / "origin.git/hooks/pre-receive"
    hook.write_text("#!/bin/sh\necho policy-rejected >&2\nexit 1\n")
    hook.chmod(0o755)
    # A dry run passes despite this server policy, so use the actual leased operation.
    git(work, "push", "--dry-run", "origin", "HEAD:main")
    with pytest.raises(ValueError, match="policy-rejected"):
        delivery.integrate(home, "repair", "genome")
    before = len(Feed(home).entries())
    delivery.check(home, "repair")
    delivery.check(home, "repair")
    assert delivery.load(home, "repair")["phase"] == "blocked"
    assert task_state.select_task(Feed(home).entries(), "genome") is None
    assert len(Feed(home).entries()) == before
    hook.unlink()
    delivery.check(home, "repair")
    assert delivery.load(home, "repair")["phase"] == "integrated"
