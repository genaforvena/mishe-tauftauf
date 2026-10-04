from datetime import datetime, timezone
from subprocess import CompletedProcess

import pytest

from mishe_tauftauf import seed, task_state
from mishe_tauftauf.feed import Feed


def supervisor(home, monkeypatch):
    sent = []
    from mishe_tauftauf import tmux
    monkeypatch.setattr(tmux, "owns_session", lambda *args: True)
    monkeypatch.setattr(tmux, "_pane_stopped_or_dead", lambda *args: False)
    monkeypatch.setattr(tmux, "capture_raw", lambda *args: seed.capture_raw(*args))
    monkeypatch.setattr(seed, "owns_session", lambda *args: True)
    monkeypatch.setattr(seed, "_pane_stopped_or_dead", lambda *args: False)
    monkeypatch.setattr(seed, "capture_raw", lambda *args: "STATE: GREEN\n-- pane live " +
                        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") + " · refresh 5s · ticks every frame --\n")
    from mishe_tauftauf import dashboard
    monkeypatch.setattr(dashboard, "read", lambda *args: (seed.capture_raw("session", "genome"), True))
    monkeypatch.setattr(seed, "_mind_ready", lambda *args: True)
    monkeypatch.setattr(seed, "_tmux", lambda *args, **kwargs: CompletedProcess(args, 0, b"0\n"))
    monkeypatch.setattr(seed, "_send", lambda target, prompt: sent.append(prompt))
    return sent


def settle(home, wake, monkeypatch, continue_task=False):
    note = home / "handoff.md"
    note.write_text("Checked the exact task step.\n")
    seed.yield_wake(home, "genome", wake, note, continue_task=continue_task, result="verified")
    Feed(home).append("seed", f"seed clear genome after={wake}\nMind cleared.")


def choose(home, wake, identity, owner="genome"):
    proof = home / "selection.txt"
    proof.write_text("The selected bounded step advances this task's acceptance; prior effects reconciled.")
    task_state.claim(home, identity, owner, wake, "Advance this useful checked step.", proof)


def test_canonical_wake_leaves_work_choice_to_mind(tmp_path, monkeypatch):
    sent = supervisor(tmp_path, monkeypatch)
    Feed(tmp_path).append('operator', '[dm] to=genome\nInvestigate the missing evidence and choose a bounded next action.')
    assert seed.tick(tmp_path, 'session', 'genome').startswith('wake ')
    assert 'Choose useful work' in Feed(tmp_path).entries()[-1].body
    assert 'TASK TO ADVANCE' not in sent[-1]
    assert 'missing evidence' in sent[-1]
    assert not task_state.pending_tasks(Feed(tmp_path).entries())


def test_continue_requests_new_turn_without_ledger_step(tmp_path, monkeypatch):
    supervisor(tmp_path, monkeypatch)
    attempt = int(seed.tick(tmp_path, 'session', 'genome').split()[-1])
    settle(tmp_path, attempt, monkeypatch, continue_task=True)
    assert seed.tick(tmp_path, 'session', 'genome', self_pick_seconds=0).startswith('wake ')
    assert not any(e.body.startswith('[work]') for e in Feed(tmp_path).entries())


def test_stable_settled_wall_waits_until_addressed_message(tmp_path, monkeypatch):
    sent = supervisor(tmp_path, monkeypatch)
    attempt = int(seed.tick(tmp_path, 'session', 'genome').split()[-1])
    settle(tmp_path, attempt, monkeypatch)
    assert 'waiting' in seed.tick(tmp_path, 'session', 'genome', self_pick_seconds=0)
    Feed(tmp_path).append('witness', 'Routine witness status.')
    assert 'waiting' in seed.tick(tmp_path, 'session', 'genome', self_pick_seconds=0)
    Feed(tmp_path).append('witness', '[dm] to=genome\nReview fresh resolver evidence.')
    assert seed.tick(tmp_path, 'session', 'genome', self_pick_seconds=0).startswith('wake ')
    assert 'fresh resolver evidence' in sent[-1]


def test_missing_presentation_lease_stays_unknown_without_send(tmp_path, monkeypatch):
    sent = supervisor(tmp_path, monkeypatch)
    from mishe_tauftauf import tmux
    monkeypatch.setattr(tmux, 'capture_raw', lambda *args: 'STATE: GREEN\n')
    assert 'UNKNOWN' in seed.tick(tmp_path, 'session', 'genome')
    assert not sent
    assert not any(e.body.startswith('seed wake ') for e in Feed(tmp_path).entries())
