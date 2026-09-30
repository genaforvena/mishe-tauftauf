import json
import pytest

from mishe_tauftauf.feed import Feed
from mishe_tauftauf import task_state


def task(home, identity, owner="genome"):
    Feed(home).append("operator", f"[task] {identity} owner={owner} source=test acceptance=checked retry=event")


def queued(home, identity, producer="senses"):
    # Historical record fixture: priority must be explicit, never inferred from an ID.
    return Feed(home).append("operator", f"[landing] {identity}\n" + json.dumps({
        "producer": producer, "evidence": "/checked.md", "evidence_sha256": "a" * 64,
        "reason": "reviewed delivery", "owner": "genome"}), task_control=True)


def test_landing_precedes_older_work_and_retains_place_after_progress(tmp_path):
    task(tmp_path, "old-investigation")
    task(tmp_path, "delivery-a")
    queued(tmp_path, "delivery-a")
    task(tmp_path, "delivery-b")
    queued(tmp_path, "delivery-b")
    path = tmp_path / "evidence.md"
    path.write_text("reviewed diff checked")
    task_state.set_step(tmp_path, "delivery-a", "genome", "commit reviewed diff", "review finished", path)
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "delivery-a"


def test_waiting_landing_requires_retry_and_active_wake_remains_reserved(tmp_path):
    task(tmp_path, "work")
    task(tmp_path, "delivery")
    queued(tmp_path, "delivery")
    path = tmp_path / "evidence.md"
    path.write_text("push failed")
    task_state.wait_for(tmp_path, "delivery", "genome", "retry push", "network unavailable", path,
                        retry_event="network-ready", retry_at="2099-01-01T00:00:00Z")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "work"
    task_state.signal(tmp_path, "network-ready", "operator", path, "network works")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "delivery"
    Feed(tmp_path).append("seed", "seed wake genome observation=1 task=delivery")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "work"


def test_unregistered_name_and_other_owners_keep_normal_order(tmp_path):
    task(tmp_path, "work")
    task(tmp_path, "important-landing")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "work"
    task(tmp_path, "health-work", "health")
    task_state.offer(tmp_path, "important-landing", "genome", ["health"], _proof(tmp_path))
    queued(tmp_path, "important-landing")
    assert task_state.select_task(Feed(tmp_path).entries(), "health").identity == "health-work"


def _proof(home):
    path = home / "proof.md"
    path.write_text("source-bound reviewed candidate")
    return path


def test_registration_is_owner_checked_and_duplicate_keeps_first_position(tmp_path):
    from mishe_tauftauf import landing
    task(tmp_path, "delivery")
    task(tmp_path, "other", "health")
    path = _proof(tmp_path)
    with pytest.raises(ValueError, match="open genome-owned"):
        landing.register(tmp_path, "other", "operator", "health", "reviewed", path)
    with pytest.raises(ValueError, match="genome or operator"):
        landing.register(tmp_path, "delivery", "senses", "senses", "reviewed", path)
    first = landing.register(tmp_path, "delivery", "operator", "senses", "reviewed", path)
    landing.register(tmp_path, "delivery", "genome", "senses", "updated proof", path)
    assert landing.registrations(Feed(tmp_path).entries())["delivery"] == first.sequence
    assert "HOLD" in landing.line(Feed(tmp_path).entries())
    Feed(tmp_path).append("genome", "[done] delivery — commit and push checked")
    assert landing.queue(Feed(tmp_path).entries()) == []
    assert "CLEAR" in landing.line(Feed(tmp_path).entries())


def test_registration_does_not_rearm_consumed_attempt(tmp_path):
    from mishe_tauftauf import landing
    task(tmp_path, "delivery")
    task_state.record_attempt(tmp_path, task_state.select_task(Feed(tmp_path).entries(), "genome"), 9, 4)
    landing.register(tmp_path, "delivery", "operator", "senses", "reviewed", _proof(tmp_path))
    assert task_state.select_task(Feed(tmp_path).entries(), "genome") is None
    assert "reserved or retry" in landing.line(Feed(tmp_path).entries())


def test_cli_production_gate_remains_closed_for_blocked_landing(tmp_path, capsys):
    from mishe_tauftauf.cli import main
    task(tmp_path, "delivery")
    path = _proof(tmp_path)
    base = ["--home", str(tmp_path), "task"]
    assert main(base + ["production-check"]) == 0
    assert main(base + ["landing", "delivery", "--source", "operator", "--producer", "senses",
                        "--reason", "reviewed", "--evidence", str(path)]) == 0
    task_state.wait_for(tmp_path, "delivery", "genome", "retry push", "network failed", path,
                        retry_event="network-ready", retry_at="2099-01-01T00:00:00Z")
    assert main(base + ["production-check"]) == 1
    assert "HOLD" in capsys.readouterr().out
    assert main(base + ["landing-status"]) == 0


def test_helper_claim_does_not_erase_delivery_debt(tmp_path):
    from mishe_tauftauf import landing
    task(tmp_path, "delivery")
    path = _proof(tmp_path)
    landing.register(tmp_path, "delivery", "operator", "senses", "reviewed", path)
    task_state.offer(tmp_path, "delivery", "genome", ["health"], path)
    selected = task_state.select_task(Feed(tmp_path).entries(), "health")
    task_state.record_attempt(tmp_path, selected, 9, 4, owner="health")
    assert landing.queue(Feed(tmp_path).entries())[0].owner == "health"
    assert "HOLD" in landing.line(Feed(tmp_path).entries())


def test_landing_cannot_wait_on_unproduced_event_only(tmp_path):
    task(tmp_path, "delivery")
    queued(tmp_path, "delivery")
    with pytest.raises(ValueError, match="production step"):
        task_state.wait_for(tmp_path, "delivery", "genome", "build candidate", "manifest missing",
                            _proof(tmp_path), retry_event="manifest-ready", producer="genome")
    with pytest.raises(ValueError, match="producer task"):
        task_state.wait_for(tmp_path, "delivery", "genome", "request review", "review missing",
                            _proof(tmp_path), retry_event="review-ready", producer="witness")


def test_landing_dependency_inherits_priority_and_preserves_reservations(tmp_path):
    from mishe_tauftauf import landing
    task(tmp_path, "old-work")
    task(tmp_path, "old-review", "witness")
    task(tmp_path, "delivery")
    queued(tmp_path, "delivery")
    path = _proof(tmp_path)
    task_state.add_task(tmp_path, "prepare", "genome", "build candidate", "local preparation", path, parent="delivery")
    task_state.add_task(tmp_path, "review", "witness", "review candidate", "independent review", path)
    task_state.wait_for(tmp_path, "prepare", "genome", "apply review", "independent review required",
                        path, retry_task="review", producer="witness")
    task_state.wait_for(tmp_path, "delivery", "genome", "commit candidate", "preparation required",
                        path, retry_task="prepare")
    assert task_state.select_task(Feed(tmp_path).entries(), "witness").identity == "review"
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "old-work"
    task_state.finish(tmp_path, "review", "witness", "review checked", path)
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "prepare"
    assert "next=prepare" in landing.line(Feed(tmp_path).entries())
    Feed(tmp_path).append("seed", "seed wake genome observation=1 task=prepare")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "old-work"


def test_older_waiting_delivery_dependency_precedes_newer_ready_delivery(tmp_path):
    task(tmp_path, "first")
    queued(tmp_path, "first")
    task(tmp_path, "second")
    queued(tmp_path, "second")
    proof = _proof(tmp_path)
    task_state.add_task(tmp_path, "prepare-first", "genome", "prepare", "local preparation", proof, parent="first")
    task_state.wait_for(tmp_path, "first", "genome", "commit", "needs preparation", proof,
                        retry_task="prepare-first")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "prepare-first"


def test_registration_and_new_dependency_cannot_import_unscheduled_wait(tmp_path):
    from mishe_tauftauf import landing
    task(tmp_path, "delivery")
    task(tmp_path, "unproduced", "witness")
    proof = _proof(tmp_path)
    task_state.wait_for(tmp_path, "unproduced", "witness", "review bytes", "candidate missing", proof,
                        retry_event="candidate-ready")
    task_state.wait_for(tmp_path, "delivery", "genome", "commit", "review missing", proof,
                        retry_task="unproduced")
    with pytest.raises(ValueError, match="landing prerequisite unproduced"):
        landing.register(tmp_path, "delivery", "operator", "senses", "prepared delivery", proof)
    task_state.set_step(tmp_path, "delivery", "genome", "prepare", "candidate scoped", proof)
    landing.register(tmp_path, "delivery", "operator", "senses", "prepared delivery", proof)
    with pytest.raises(ValueError, match="landing prerequisite unproduced"):
        task_state.wait_for(tmp_path, "delivery", "genome", "commit", "review missing", proof,
                            retry_task="unproduced")


def test_active_delivery_and_exact_evidence_paths_are_visible(tmp_path):
    from mishe_tauftauf import landing
    proof = _proof(tmp_path)
    task_state.add_task(tmp_path, "delivery", "genome", "commit", "review passed", proof)
    queued(tmp_path, "delivery")
    task(tmp_path, "later")
    queued(tmp_path, "later")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1 task=delivery")
    selected = task_state.states(Feed(tmp_path).entries())["delivery"]
    task_state.record_attempt(tmp_path, selected, wake.sequence, 1)
    assert str(proof.resolve()) in "\n".join(task_state.lines(Feed(tmp_path).entries(), "genome"))
    line = landing.line(Feed(tmp_path).entries())
    assert f"active=genome:delivery@{wake.sequence}" in line
    assert "next=later" in line
    assert "reserved to finish, not blocked" in line
