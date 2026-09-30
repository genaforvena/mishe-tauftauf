"""Old landing records remain readable without imposing scheduling policy."""
import json
import pytest
from mishe_tauftauf import landing, task_state
from mishe_tauftauf.feed import Feed


def task(home, identity, owner="genome"):
    Feed(home).append("operator", f"[task] {identity} owner={owner} acceptance=checked")


def queued(home, identity):
    return Feed(home).append("operator", f"[landing] {identity}\n" + json.dumps({
        "producer": "senses", "evidence": "/checked.md", "evidence_sha256": "a" * 64,
        "reason": "historical delivery", "owner": "genome"}), task_control=True)


def test_historical_registration_has_no_priority_or_global_hold(tmp_path):
    from mishe_tauftauf.cli import main
    task(tmp_path, "earlier")
    task(tmp_path, "legacy")
    first = queued(tmp_path, "legacy")
    queued(tmp_path, "legacy")
    entries = Feed(tmp_path).entries()
    assert landing.registrations(entries)["legacy"] == first.sequence
    assert task_state.select_task(entries, "genome").identity == "earlier"
    assert landing.queue(entries)[0].identity == "legacy"
    assert "source production allowed" in landing.line(entries)
    assert main(["--home", str(tmp_path), "task", "production-check"]) == 0


def test_legacy_event_wait_is_allowed_and_children_do_not_inherit_priority(tmp_path):
    task(tmp_path, "earlier", "witness")
    task(tmp_path, "legacy")
    queued(tmp_path, "legacy")
    proof = tmp_path / "proof.txt"
    proof.write_text("candidate unavailable")
    task_state.add_task(tmp_path, "child", "witness", "prepare", "owned preparation", proof, parent="legacy")
    task_state.wait_for(tmp_path, "legacy", "genome", "integrate", "waiting on facts", proof,
                        retry_event="candidate-published")
    assert task_state.select_task(Feed(tmp_path).entries(), "witness").identity == "earlier"
    assert task_state.select_task(Feed(tmp_path).entries(), "genome") is None
    task_state.signal(tmp_path, "candidate-published", "senses", proof, "candidate published")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "legacy"


def test_historical_registration_does_not_rearm_or_erase_active_wake(tmp_path):
    task(tmp_path, "legacy")
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1 task=legacy")
    selected = task_state.registry(Feed(tmp_path).entries())["legacy"]
    task_state.record_attempt(tmp_path, selected, wake.sequence, 1)
    queued(tmp_path, "legacy")
    assert task_state.pending_tasks(Feed(tmp_path).entries())[("genome", wake.sequence)] == "legacy"
    assert task_state.select_task(Feed(tmp_path).entries(), "genome") is None


def test_new_raw_draft_registration_requires_committed_candidate(tmp_path):
    with pytest.raises(ValueError, match="delivery submit"):
        landing.register(tmp_path, "draft", "operator", "senses", "unfinished draft", tmp_path / "proof")
