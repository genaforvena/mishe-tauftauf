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
                        retry_event="network-ready")
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
                        retry_event="network-ready")
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
