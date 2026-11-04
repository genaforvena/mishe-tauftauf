"""Canonical defaults exercise fresh plants without a trial installer."""
import json
import subprocess
import pytest
from mishe_tauftauf import wall, observations, ci_watch, post_check
from mishe_tauftauf.feed import Feed


from datetime import datetime, timezone













def test_missing_configuration_means_open_wall_workflow(tmp_path):
    assert wall.settings(tmp_path).get("mode") == "wall"
    assert not wall.settings(tmp_path).get("until")


def test_mode_is_not_a_feature_switch(tmp_path):
    (tmp_path / "coordination-mode.json").write_text('{"mode":"ledger"}')
    with pytest.raises(ValueError, match="retired"):
        wall.settings(tmp_path)


def test_configuration_can_omit_historical_mode_key(tmp_path):
    (tmp_path / "coordination-mode.json").write_text('{"silence_seconds":900}')
    assert wall.settings(tmp_path)["mode"] == "wall"




def test_default_feed_never_runs_semantic_publication_gate(tmp_path, monkeypatch):
    (tmp_path / "publication-check.json").write_text('{"command":["missing-checker"]}')
    def reject(*a, **k):
        raise AssertionError("legacy semantic publication gate")
    monkeypatch.setattr(post_check, "require", reject)
    assert Feed(tmp_path).append("genome", "Planning an authorized experiment; checks follow.").sequence == 1






def test_default_ci_routes_a_message_without_delivery_scheduler(tmp_path, monkeypatch):
    from mishe_tauftauf import delivery
    monkeypatch.setattr(ci_watch, "read", lambda h: {"state":"fail", "sha":"a"*40, "run":"1", "url":"https://example.invalid", "detail":"failed"})
    monkeypatch.setattr(delivery, "check_all", lambda h: (_ for _ in ()).throw(AssertionError("retired delivery scheduler")))
    ci_watch.tick(tmp_path)
    bodies = [e.body for e in Feed(tmp_path).entries()]
    assert any(b.startswith("[dm] to=genome") for b in bodies)
    assert not any(b.startswith("[task]") for b in bodies)


def test_chat_keeps_structured_state_in_referenced_records(tmp_path):
    from mishe_tauftauf.post_check import CorrectionRequired
    with pytest.raises(CorrectionRequired):
        Feed(tmp_path).append("genome", '{"status":"done","counts":[1,2,3],"nested":{"a":1}}')
    assert Feed(tmp_path).tail_sequence() == 0


@pytest.mark.parametrize("command", [["task", "add", "obsolete", "--owner", "genome", "--next-step", "work", "--reason", "reason", "--evidence", "missing"], ["delivery", "check", "obsolete"]])
def test_retired_cli_cannot_restart_alternate_workflow(tmp_path, command):
    from mishe_tauftauf.cli import main
    try:
        status = main(["--home", str(tmp_path), *command])
    except SystemExit as exc:
        status = exc.code
    assert status != 0
    assert Feed(tmp_path).tail_sequence() == 0






def _rotated_journal(home, settled, **overrides):
    """A wall-clear journal stranded in the rotated phase, as a crash leaves it."""
    journal = {"phase": "rotated", "session": "session", "role": "genome",
               "settled": settled, "after_pid": "102",
               "launch_command": ["env", "PYTHONPATH=/reviewed/src", "/site/minds/genome"]}
    journal.update(overrides)
    checks = home / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    path = checks / f"wall-clear-genome-{settled}.json"
    path.write_text(json.dumps(journal))
    return path


def _pane_command(pid, dead="0", start="env PYTHONPATH=/reviewed/src /site/minds/genome"):
    """A fake display-message reader: pane pid, dead flag, start command."""
    from subprocess import CompletedProcess
    def command(*args, **kwargs):
        value = (pid if args[-1] == "#{pane_pid}" else
                 dead if args[-1] == "#{pane_dead}" else
                 start if args[-1] == "#{pane_start_command}" else "0")
        return CompletedProcess(args, 0, value.encode(), b"")
    return command


def _respawning_pane_command(before, after, dead="0",
                             start="env PYTHONPATH=/reviewed/src /site/minds/genome"):
    """A fake tmux client whose respawn-pane actually re-pids the pane."""
    from subprocess import CompletedProcess
    pid = [before]
    def command(*args, **kwargs):
        if args[0] == "respawn-pane":
            pid[0] = after
        value = (pid[0] if args[-1] == "#{pane_pid}" else
                 dead if args[-1] == "#{pane_dead}" else
                 start if args[-1] == "#{pane_start_command}" else "0")
        return CompletedProcess(args, 0, value.encode(), b"")
    return command


def _settled_genome(tmp_path):
    """A settled genome wake the supervisor now owes a clear for."""
    wake = Feed(tmp_path).append("seed", "seed wake genome observation=1\nInvestigate.")
    note = tmp_path / "note.md"
    note.write_text("Investigated source; next trigger is changed evidence.")
    seed.yield_wake(tmp_path, "genome", wake.sequence, note, result="verified")
    return wake














































def _committed_journal(home, settled, **overrides):
    """A wall-clear journal a renumbered feed's number collides with.

    Its phase is committed and both recorded pids are long dead: the exact
    research-methods 38264 shape of 2026-10-14, where the feed had been
    renumbered and settled 38264 again named a different turn.
    """
    journal = {"phase": "committed", "session": "session", "role": "genome",
               "settled": settled, "before_pid": "150829", "after_pid": "204172",
               "launch_command": ["env", "PYTHONPATH=/old/src", "/site/minds/genome"]}
    journal.update(overrides)
    checks = home / "checks"
    checks.mkdir(parents=True, exist_ok=True)
    path = checks / f"wall-clear-genome-{settled}.json"
    path.write_text(json.dumps(journal))
    return path




