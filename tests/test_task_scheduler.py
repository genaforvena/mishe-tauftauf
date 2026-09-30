from datetime import datetime, timezone
from subprocess import CompletedProcess

import pytest

from mishe_tauftauf import seed, task_state
from mishe_tauftauf.feed import Feed


def supervisor(home, monkeypatch):
    sent = []
    monkeypatch.setattr(seed, "owns_session", lambda *args: True)
    monkeypatch.setattr(seed, "_pane_stopped_or_dead", lambda *args: False)
    monkeypatch.setattr(seed, "capture_raw", lambda *args: "STATE: GREEN\n-- pane live " +
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") + " · refresh 5s · ticks every frame --\n")
    monkeypatch.setattr(seed, "_mind_ready", lambda *args: True)
    monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: CompletedProcess(args, 0, b"0\n"))
    monkeypatch.setattr(seed, "_send", lambda target, prompt: sent.append(prompt))
    return sent


def settle(home, wake, monkeypatch, continue_task=False):
    note = home / "handoff.md"
    note.write_text("Checked the exact task step.\n")
    seed.yield_wake(home, "genome", wake, note, continue_task=continue_task, result="verified")
    Feed(home).append("seed", f"seed clear genome after={wake}\nMind cleared.")


def test_waited_task_is_not_retaken_by_self_pick_or_continue(tmp_path, monkeypatch):
    sent = supervisor(tmp_path, monkeypatch)
    Feed(tmp_path).append("operator", "[task] repair owner=genome acceptance=fix")
    wake = int(seed.tick(tmp_path, "session", "genome").split()[-1])
    assert "TASK TO ADVANCE: repair" in sent[-1]
    with pytest.raises(ValueError, match="next step|retry"):
        settle(tmp_path, wake, monkeypatch, continue_task=True)
    path = tmp_path / "proof.md"
    path.write_text("caller absent")
    task_state.wait_for(tmp_path, "repair", "genome", "integrate caller", "caller absent", path,
                        retry_event="caller-ready")
    settle(tmp_path, wake, monkeypatch)
    assert "waiting" in seed.tick(tmp_path, "session", "genome", self_pick_seconds=0.000001)
    assert len(sent) == 1
    Feed(tmp_path).append("operator", "[task] other owner=genome acceptance=check")
    next_wake = int(seed.tick(tmp_path, "session", "genome").split()[-1])
    assert "TASK TO ADVANCE: other" in sent[-1]
    settle(tmp_path, next_wake, monkeypatch)
    task_state.signal(tmp_path, "caller-ready", "operator", path, "caller now ready")
    assert seed.tick(tmp_path, "session", "genome").startswith("wake ")
    assert "TASK TO ADVANCE: repair" in sent[-1]


def test_checked_progress_continues_same_task(tmp_path, monkeypatch):
    sent = supervisor(tmp_path, monkeypatch)
    Feed(tmp_path).append("operator", "[task] repair owner=genome acceptance=fix")
    wake = int(seed.tick(tmp_path, "session", "genome").split()[-1])
    path = tmp_path / "proof.md"
    path.write_text("implemented and checked")
    task_state.set_step(tmp_path, "repair", "genome", "independent review", "implementation checked", path)
    settle(tmp_path, wake, monkeypatch, continue_task=True)
    assert seed.tick(tmp_path, "session", "genome").startswith("wake ")
    assert "independent review" in sent[-1]
    receipts = [e for e in Feed(tmp_path).entries() if e.body.startswith("[work]")]
    assert "task=repair" in receipts[-1].body.splitlines()[0]


def test_duplicate_announcement_does_not_resume_waiting_or_terminal_task(tmp_path):
    Feed(tmp_path).append("operator", "[task] repair owner=genome acceptance=fix")
    path = tmp_path / "proof.md"
    path.write_text("caller absent")
    task_state.wait_for(tmp_path, "repair", "genome", "integrate", "missing", path, retry_event="ready")
    Feed(tmp_path).append("witness", "[task] repair owner=genome acceptance=fix")
    assert seed._external_event(tmp_path, "genome") is None


def test_pending_wake_reserves_task_even_before_attempt_record(tmp_path):
    Feed(tmp_path).append("operator", "[task] repair owner=genome acceptance=fix")
    path = tmp_path / "proof.md"
    path.write_text("health can perform this check")
    task_state.offer(tmp_path, "repair", "genome", ["health"], path)
    wake = Feed(tmp_path).append("seed", "seed wake health observation=1 task=repair\nDelivery pending.")
    assert seed._state(tmp_path, "health")[1] == wake.sequence
    assert task_state.select_task(Feed(tmp_path).entries(), "genome") is None
    assert task_state.select_task(Feed(tmp_path).entries(), "health") is None


def test_helper_cannot_take_next_step_before_owner_yields(tmp_path, monkeypatch):
    supervisor(tmp_path, monkeypatch)
    Feed(tmp_path).append("operator", "[task] repair owner=genome acceptance=fix")
    path = tmp_path / "proof.md"
    path.write_text("health can check this")
    task_state.offer(tmp_path, "repair", "genome", ["health"], path)
    wake = int(seed.tick(tmp_path, "session", "genome").split()[-1])
    path.write_text("implementation checked")
    task_state.set_step(tmp_path, "repair", "genome", "verify", "implemented", path)
    assert task_state.select_task(Feed(tmp_path).entries(), "health") is None
    settle(tmp_path, wake, monkeypatch)
    assert task_state.select_task(Feed(tmp_path).entries(), "health").identity == "repair"


def test_completed_task_keeps_receipt_identity_and_cannot_continue_empty_work(tmp_path, monkeypatch):
    supervisor(tmp_path, monkeypatch)
    Feed(tmp_path).append("operator", "[task] repair owner=genome acceptance=fix")
    wake = int(seed.tick(tmp_path, "session", "genome").split()[-1])
    Feed(tmp_path).append("genome", "[done] repair — checked artifact")
    with pytest.raises(ValueError, match="next step|retry"):
        settle(tmp_path, wake, monkeypatch, continue_task=True)
    settle(tmp_path, wake, monkeypatch)
    receipt = next(e for e in Feed(tmp_path).entries() if e.body.startswith("[work]"))
    assert "task=repair" in receipt.body.splitlines()[0]
    Feed(tmp_path).append("genome", "[done] repair — checked")
    assert seed._external_event(tmp_path, "genome") is None
