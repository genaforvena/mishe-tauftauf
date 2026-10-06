from datetime import datetime, timedelta, timezone

import pytest

from mishe_tauftauf.feed import Feed
from mishe_tauftauf import task_state


def task(home, identity="repair", owner="genome"):
    return Feed(home).append("operator", f"[task] {identity} owner={owner} source=test acceptance=checked retry=event")


def evidence(home, text="checked missing prerequisite"):
    path = home / "evidence.md"
    path.write_text(text)
    return path


def test_wait_survives_restart_and_signal_is_consumed_once(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    task_state.wait_for(tmp_path, "repair", "genome", "integrate caller", "caller missing", path,
                        retry_event="caller-ready")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome") is None
    task_state.signal(tmp_path, "irrelevant", "operator", path, "unrelated change")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome") is None
    task_state.signal(tmp_path, "caller-ready", "operator", path, "caller now exists")
    ready = task_state.select_task(Feed(tmp_path).entries(), "genome")
    assert ready.identity == "repair"
    task_state.record_attempt(tmp_path, ready, wake=50, observation=4)
    assert task_state.select_task(Feed(tmp_path).entries(), "genome") is None
    state = task_state.states(Feed(tmp_path).entries())["repair"]
    assert state.attempt_wake == 50
    assert state.next_step == "integrate caller"


def test_due_retry_and_other_ready_task(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    due = datetime.now(timezone.utc) + timedelta(minutes=5)
    task_state.wait_for(tmp_path, "repair", "genome", "sample", "source absent", path, retry_at=due.isoformat())
    assert task_state.select_task(Feed(tmp_path).entries(), "genome", now=due-timedelta(seconds=1)) is None
    task_state.record_attempt(tmp_path, task_state.select_task(Feed(tmp_path).entries(), "genome", now=due), 9, 4)
    assert task_state.select_task(Feed(tmp_path).entries(), "genome", now=due+timedelta(hours=1)) is None
    task(tmp_path, "other")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "other"


def test_next_step_requires_distinct_step_or_changed_evidence(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    task_state.set_step(tmp_path, "repair", "genome", "implement", "diagnosis complete", path)
    ready = task_state.select_task(Feed(tmp_path).entries(), "genome")
    task_state.record_attempt(tmp_path, ready, 9, 4)
    with pytest.raises(ValueError, match="unchanged"):
        task_state.set_step(tmp_path, "repair", "genome", "implement", "diagnosis complete", path)
    path.write_text("implementation changed and checked")
    task_state.set_step(tmp_path, "repair", "genome", "verify", "implementation ready", path)
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").next_step == "verify"


def test_reject_wrong_owner_missing_evidence_and_closed_tasks(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    with pytest.raises(ValueError, match="owner"):
        task_state.set_step(tmp_path, "repair", "senses", "check", "progress", path)
    with pytest.raises(ValueError, match="evidence"):
        task_state.set_step(tmp_path, "repair", "genome", "check", "progress", tmp_path/"missing")
    Feed(tmp_path).append("genome", "[done] repair — checked")
    with pytest.raises(ValueError, match="open"):
        task_state.set_step(tmp_path, "repair", "genome", "check", "progress", path)


def test_wait_requires_exact_event_or_timezone_deadline(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    with pytest.raises(ValueError, match="retry"):
        task_state.wait_for(tmp_path, "repair", "genome", "check", "missing", path)
    with pytest.raises(ValueError, match="timezone"):
        task_state.wait_for(tmp_path, "repair", "genome", "check", "missing", path, retry_at="2026-10-01T00:00:00")


def test_idle_helper_claims_offered_ready_task_once(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    assert task_state.select_task(Feed(tmp_path).entries(), "health") is None
    task_state.offer(tmp_path, "repair", "genome", ["health", "senses"], path)
    helper = task_state.select_task(Feed(tmp_path).entries(), "health")
    assert helper.identity == "repair"
    task_state.record_attempt(tmp_path, helper, 9, 4, owner="health")
    assert task_state.states(Feed(tmp_path).entries())["repair"].owner == "health"
    assert task_state.select_task(Feed(tmp_path).entries(), "senses") is None
    from mishe_tauftauf.seed_board import open_tasks
    assert open_tasks(Feed(tmp_path).entries())[0].owner == "health"


def test_offer_does_not_steal_active_or_waiting_task_and_own_work_first(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    task_state.record_attempt(tmp_path, task_state.select_task(Feed(tmp_path).entries(), "genome"), 9, 4)
    task_state.offer(tmp_path, "repair", "genome", ["health"], path)
    assert task_state.select_task(Feed(tmp_path).entries(), "health") is None
    task_state.set_step(tmp_path, "repair", "genome", "verify", "implementation checked", path)
    task(tmp_path, "own-health-work", "health")
    assert task_state.select_task(Feed(tmp_path).entries(), "health").identity == "own-health-work"


def settlement(home, channel, observation, result="verified"):
    wake = Feed(home).append("seed", f"seed wake {channel} observation={observation}\n"
                                     "Read your wall, addressed messages and live sensors.").sequence
    Feed(home).append("seed", f"seed yield {channel} wake={wake}\n"
                              f"Turn settled ({result}); wall and handoff at walls/{channel}.md.")


def test_repeat_detector_fires_on_unchanged_current_settlements(tmp_path):
    from mishe_tauftauf.seed_board import repeated_no_change
    for _ in range(3):
        settlement(tmp_path, "genome", observation=42)
    assert len(repeated_no_change(Feed(tmp_path).entries(), "genome")) == 3


def test_repeat_detector_ignores_changed_observation(tmp_path):
    from mishe_tauftauf.seed_board import repeated_no_change
    for observation in (1, 2, 3):
        settlement(tmp_path, "genome", observation=observation)
    assert repeated_no_change(Feed(tmp_path).entries(), "genome") == []


def test_nonowner_cannot_close_another_roles_task(tmp_path):
    task(tmp_path)
    Feed(tmp_path).append("witness", "[done] repair — checked one audit; genome continues")
    assert "repair" in task_state.states(Feed(tmp_path).entries())


def test_closed_task_stays_closed_on_taking_in_both_views(tmp_path):
    from mishe_tauftauf.seed_board import open_tasks
    task(tmp_path)
    Feed(tmp_path).append("genome", "[done] repair — checked")
    Feed(tmp_path).append("genome", "[task] repair owner=genome")
    Feed(tmp_path).append("genome", "[taking] repair — attempting to restore")
    assert "repair" not in task_state.states(Feed(tmp_path).entries())
    assert open_tasks(Feed(tmp_path).entries()) == []


def test_explicit_reopen_and_child_finish_preserve_managed_parent(tmp_path):
    from mishe_tauftauf.seed_board import open_tasks
    task(tmp_path)
    Feed(tmp_path).append("genome", "[done] repair — accidentally closed after audit")
    path = evidence(tmp_path)
    task_state.reopen(tmp_path, "repair", "genome", "produce missing measurements",
                      "prior completion certified only an audit", path)
    task_state.add_task(tmp_path, "measure", "genome", "evaluate cached weights on CPU",
                        "training admission is blocked; independent evaluation available", path, parent="repair")
    with pytest.raises(ValueError, match="child"):
        task_state.finish(tmp_path, "repair", "genome", "all complete", path)
    task_state.finish(tmp_path, "measure", "genome", "raw measured rows checked", path)
    states = task_state.states(Feed(tmp_path).entries())
    assert set(states) == {"repair"}
    assert {t.identity for t in open_tasks(Feed(tmp_path).entries())} == {"repair"}
    # A prose done for a managed goal must not close it, even from its owner.
    Feed(tmp_path).append("genome", "[done] repair — one audit finished")
    assert "repair" in task_state.states(Feed(tmp_path).entries())


def test_fresh_artifact_with_unchanged_outcome_cannot_requeue_same_step(tmp_path):
    task(tmp_path)
    path = evidence(tmp_path)
    task_state.set_step(tmp_path, "repair", "genome", "find caller", "caller remains absent", path)
    state = task_state.select_task(Feed(tmp_path).entries(), "genome")
    task_state.record_attempt(tmp_path, state, 9, 4)
    path.write_text("new timestamp and hash, still no caller")
    with pytest.raises(ValueError, match="unchanged"):
        task_state.set_step(tmp_path, "repair", "genome", "find caller", "caller remains absent", path)


def test_producer_completion_fires_review_retry_without_polling(tmp_path):
    path = evidence(tmp_path)
    task_state.add_task(tmp_path, "measure", "genome", "CPU measurement", "cached inputs", path)
    task_state.add_task(tmp_path, "review", "witness", "review completed raw evidence", "independent review", path)
    task_state.wait_for(tmp_path, "review", "witness", "review raw output", "producer running", path,
                        retry_task="measure", producer="genome")
    assert task_state.select_task(Feed(tmp_path).entries(), "witness") is None
    task_state.finish(tmp_path, "measure", "genome", "100 rows complete", path)
    assert task_state.select_task(Feed(tmp_path).entries(), "witness").identity == "review"
    task_state.record_attempt(tmp_path, task_state.select_task(Feed(tmp_path).entries(), "witness"), 9, 4)
    assert task_state.select_task(Feed(tmp_path).entries(), "witness") is None


def test_waiting_acceptance_keeps_alternative_production_step_ready(tmp_path):
    path = evidence(tmp_path)
    task_state.add_task(tmp_path, "research", "genome", "registered acceptance", "finish research", path)
    task_state.add_task(tmp_path, "cpu-panel", "genome", "measure saved weights", "CPU evidence available", path,
                        parent="research")
    task_state.wait_for(tmp_path, "research", "genome", "registered replication", "historical GPU accounting missing", path,
                        retry_event="gpu-accounted", producer="genome", alternative="cpu-panel")
    assert task_state.select_task(Feed(tmp_path).entries(), "genome").identity == "cpu-panel"
    task_state.finish(tmp_path, "cpu-panel", "genome", "measured outputs", path)
    assert "research" in task_state.states(Feed(tmp_path).entries())
    assert not task_state.eligible(task_state.states(Feed(tmp_path).entries())["research"], Feed(tmp_path).entries())


def test_completion_dependency_cycles_rejected_before_append(tmp_path):
    path = evidence(tmp_path)
    for identity in ("landing", "deploy"):
        task_state.add_task(tmp_path, identity, "genome", identity, "owned work", path)
    task_state.wait_for(tmp_path, "landing", "genome", "land", "deploy first", path,
                        retry_task="deploy", producer="genome")
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="cycle"):
        task_state.wait_for(tmp_path, "deploy", "genome", "deploy", "land first", path,
                            retry_task="landing", producer="genome")
    assert Feed(tmp_path).read_bytes() == before


def test_repeat_detector_ignores_changed_result(tmp_path):
    from mishe_tauftauf.seed_board import repeated_no_change
    settlement(tmp_path, "genome", observation=42, result="verified")
    settlement(tmp_path, "genome", observation=42, result="verified")
    settlement(tmp_path, "genome", observation=42, result="changed")
    assert repeated_no_change(Feed(tmp_path).entries(), "genome") == []


def test_structured_writer_rejects_malformed_control_before_persisting(tmp_path):
    task(tmp_path)
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="control"):
        Feed(tmp_path).append_task_control("genome", "[task-state] repair — prose")
    assert Feed(tmp_path).read_bytes() == before


def test_attaching_child_protects_legacy_parent_from_prose_completion(tmp_path):
    task(tmp_path)
    task_state.add_task(tmp_path, "child", "genome", "produce rows", "independent evidence", evidence(tmp_path),
                        parent="repair")
    Feed(tmp_path).append("genome", "[done] repair — small step finished")
    assert set(task_state.states(Feed(tmp_path).entries())) == {"repair", "child"}


def test_already_completed_producer_makes_review_ready_immediately(tmp_path):
    path = evidence(tmp_path)
    task_state.add_task(tmp_path, "measure", "genome", "measure", "CPU available", path)
    task_state.finish(tmp_path, "measure", "genome", "rows checked", path)
    task_state.add_task(tmp_path, "review", "witness", "review", "review needed", path)
    task_state.wait_for(tmp_path, "review", "witness", "review rows", "producer completion required", path,
                        retry_task="measure", producer="genome")
    assert task_state.select_task(Feed(tmp_path).entries(), "witness").identity == "review"


def test_child_cannot_wait_on_its_parent_completion(tmp_path):
    path = evidence(tmp_path)
    task_state.add_task(tmp_path, "parent", "genome", "complete research", "goal", path)
    task_state.add_task(tmp_path, "child", "genome", "produce missing measurements", "step", path, parent="parent")
    with pytest.raises(ValueError, match="cycle"):
        task_state.wait_for(tmp_path, "child", "genome", "measure", "parent completion first", path,
                            retry_task="parent", producer="genome")


def test_reopen_child_requires_open_parent(tmp_path):
    path = evidence(tmp_path)
    task_state.add_task(tmp_path, "parent", "genome", "goal", "goal", path)
    task_state.add_task(tmp_path, "child", "genome", "measure", "step", path, parent="parent")
    task_state.finish(tmp_path, "child", "genome", "checked", path)
    task_state.finish(tmp_path, "parent", "genome", "checked", path)
    with pytest.raises(ValueError, match="parent"):
        task_state.reopen(tmp_path, "child", "genome", "correct measurement", "review found error", path)
    task_state.reopen(tmp_path, "parent", "genome", "correct result", "review found error", path)
    task_state.reopen(tmp_path, "child", "genome", "correct measurement", "review found error", path)
    assert set(task_state.states(Feed(tmp_path).entries())) == {"parent", "child"}


def test_typed_control_rejects_invalid_helper_payload_before_append(tmp_path):
    import json
    from dataclasses import asdict
    task(tmp_path)
    state = asdict(task_state.states(Feed(tmp_path).entries())["repair"])
    state["helpers"] = 17
    before = Feed(tmp_path).read_bytes()
    with pytest.raises(ValueError, match="control"):
        Feed(tmp_path).append_task_control("genome", "[task-state] repair\n" + json.dumps(state))
    assert Feed(tmp_path).read_bytes() == before


def historical_entries(*rows):
    """Decode old framed bytes without asking today's writer to admit them."""
    from mishe_tauftauf.feed import _encode_entry, parse_feed
    return parse_feed(b"".join(
        _encode_entry(sequence, "2026-09-30T00:00:00Z", source, body)
        for sequence, (source, body) in enumerate(rows, 1)))


def state_payload(**updates):
    import json
    from dataclasses import asdict
    return json.dumps({**asdict(task_state.TaskState("repair", "genome", 1)),
                       "next_step": "verify surviving board", **updates})


def test_historical_note_preserves_later_step_but_corrupt_json_fails():
    rows = [("operator", "[task] repair owner=genome source=test acceptance=checked"),
            ("genome", "[task-state] repair — historical note"),
            ("genome", "[task-state] repair\n" + state_payload())]
    assert task_state.states(historical_entries(*rows))["repair"].next_step == "verify surviving board"
    with pytest.raises(ValueError, match="invalid task state"):
        task_state.states(historical_entries(*rows, ("genome", "[task-state] repair\nnot json")))


def test_future_state_fields_preserve_known_fields_and_owner_checks():
    rows = [("operator", "[task] repair owner=genome source=test acceptance=checked")]
    body = "[task-state] repair\n" + state_payload(future_schema={"version": 2})
    state = task_state.states(historical_entries(*rows, ("genome", body)))["repair"]
    assert (state.owner, state.next_step) == ("genome", "verify surviving board")
    with pytest.raises(ValueError, match="owner mismatch"):
        task_state.states(historical_entries(*rows, ("witness", body)))


def test_historical_stale_step_does_not_reopen_closed_task():
    rows = [("operator", "[task] repair owner=genome source=test acceptance=checked"),
            ("genome", "[done] repair — checked"),
            ("genome", "[task-state] repair\n" + state_payload())]
    assert task_state.registry(historical_entries(*rows))["repair"].status == "done"
    assert task_state.states(historical_entries(*rows)) == {}
