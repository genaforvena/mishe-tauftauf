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


def test_progress_steps_do_not_trip_repeat_detector(tmp_path):
    from mishe_tauftauf.seed_board import repeated_no_change
    for wake, step in [(1, "diagnose"), (2, "implement"), (3, "review")]:
        Feed(tmp_path).append("seed", f"[work] channel=genome wake={wake} observation=1 result=verified "
                             f"continue=1 task=repair task_step={step} artifact=checked")
    assert repeated_no_change(Feed(tmp_path).entries(), "genome") == []
