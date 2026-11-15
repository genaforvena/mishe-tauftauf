"""The codex CLI engine: command shape, failure mapping and the rollout-based account window."""
import json
import time
from pathlib import Path

import pytest

from mishe_tauftauf import codex_usage, engine_codex
from mishe_tauftauf.wake import EngineExhausted, EngineRejected


def _stream(tmp_path: Path, rows: list) -> Path:
    path = tmp_path / "p.out.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_command_reads_prompt_from_stdin_and_names_the_sandbox(tmp_path):
    cmd = engine_codex.command("gpt-6-luna", tmp_path, thinking="high")
    assert cmd[:2] == ["codex", "exec"]
    assert cmd[cmd.index("-s") + 1] == engine_codex.SANDBOX
    assert cmd[cmd.index("-m") + 1] == "gpt-6-luna"
    assert cmd[-1] == "-"
    assert 'model_reasoning_effort="high"' in cmd


def test_unsupported_model_before_any_command_is_rejected_not_exhausted(tmp_path):
    error = "model is not supported when using Codex with a ChatGPT account"
    stream = _stream(tmp_path, [{"type": "thread.started"},
                                {"type": "error", "message": error}])
    assert engine_codex.refused_before_effect(stream) == error[:300]
    assert engine_codex.is_unsupported_model(error)
    assert not engine_codex.is_quota_error(error)


def test_quota_text_before_effect_is_quota(tmp_path):
    stream = _stream(tmp_path, [{"type": "turn.failed", "error": {"message": "You have hit your usage limit"}}])
    error = engine_codex.refused_before_effect(stream)
    assert error and engine_codex.is_quota_error(error)


def test_a_tool_effect_means_no_refusal_is_reported(tmp_path):
    stream = _stream(tmp_path, [
        {"type": "item.started", "item": {"type": "command_execution", "command": "ls"}},
        {"type": "turn.failed", "error": {"message": "usage limit reached"}}])
    assert engine_codex.acted(stream)
    assert engine_codex.refused_before_effect(stream) is None
    assert engine_codex.spent_quota_after_effect(stream) == "usage limit reached"


def test_tool_output_quoting_quota_text_is_not_an_error(tmp_path):
    stream = _stream(tmp_path, [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "usage limit reached in docs"}}])
    assert engine_codex.refused_before_effect(stream) is None


def test_malformed_lines_are_skipped(tmp_path):
    path = tmp_path / "p.out.jsonl"
    path.write_text('not json\n{"type": "error", "message": "x"\n' + json.dumps({"type": "error", "error": {"message": "usage limit"}}) + "\n")
    assert engine_codex.refused_before_effect(path) == "usage limit"


def _rollout(root: Path, name: str, rows: list) -> Path:
    day = root / "2026" / "11" / "15"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def _token_count(stamp: str, used5: float, used7: float, reset5: float, reset7: float, limit="codex") -> dict:
    return {"timestamp": stamp, "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "limit_id": limit,
        "primary": {"used_percent": used5, "window_minutes": 300, "resets_at": reset5},
        "secondary": {"used_percent": used7, "window_minutes": 10080, "resets_at": reset7}}}}


def test_account_row_is_fresh_only_while_the_reading_is_recent(tmp_path):
    now = time.time()
    from datetime import datetime, timezone
    stamp = datetime.fromtimestamp(now - 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _rollout(tmp_path, "a", [{"type": "session_meta"}, _token_count(stamp, 4.0, 40.0, now + 3600, now + 86400)])
    row = codex_usage.account_row(now, sessions=tmp_path)
    assert row["provider"] == "openai-codex"
    assert row["state"] == "FRESH"
    assert row["windows"]["5h"]["remaining"] == pytest.approx(0.96)
    assert row["windows"]["7d"]["remaining"] == pytest.approx(0.60)


def test_old_reading_is_stale_not_fresh(tmp_path):
    now = time.time()
    _rollout(tmp_path, "old", [_token_count("2026-11-08T15:03:28.150Z", 4.0, 93.0, now + 3600, now + 86400)])
    row = codex_usage.account_row(now, sessions=tmp_path)
    assert row["state"] == "STALE"
    assert all(w["state"] == "STALE" for w in row["windows"].values())


def test_other_limit_ids_and_missing_sessions_give_no_row(tmp_path):
    now = time.time()
    _rollout(tmp_path, "b", [_token_count("2026-11-15T14:00:00.000Z", 1.0, 1.0, now + 60, now + 60, limit="other")])
    assert codex_usage.account_row(now, sessions=tmp_path) is None
    assert codex_usage.account_row(now, sessions=tmp_path / "missing") is None


def test_refresh_probes_only_when_the_reading_is_old(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(codex_usage.shutil, "which", lambda name: "/bin/codex")
    run = lambda argv, **kw: calls.append((argv, kw))
    assert codex_usage.refresh(1_000_000.0, sessions=tmp_path, run=run) is True  # no reading at all
    cmd, kw = calls[0]
    assert cmd[:2] == ["/bin/codex", "exec"] and "read-only" in cmd and cmd[cmd.index("-m") + 1] == "gpt-6-luna"
    assert kw["input"] and kw["timeout"] == codex_usage.PROBE_TIMEOUT_S
    monkeypatch.setattr(codex_usage, "latest_reading", lambda sessions=None, **k: {"at": 999_900.0, "rate_limits": {}})
    assert codex_usage.refresh(1_000_000.0, sessions=tmp_path, run=run) is False  # fresh enough
    monkeypatch.setattr(codex_usage.shutil, "which", lambda name: None)
    assert codex_usage.refresh(2_000_000.0, sessions=tmp_path, run=run) is False  # no CLI: nothing runs
    assert len(calls) == 1


def test_a_probe_that_yields_no_reading_does_not_repeat_within_the_cooldown(tmp_path, monkeypatch):
    """A probe that produces no reading must not become a stream of model calls.

    Six probes ran in four minutes on 2026-11-15 because nothing remembered that the
    previous probe had already failed; each one is a real call on the codex-a account.
    """
    calls = []
    monkeypatch.setattr(codex_usage.shutil, "which", lambda name: "/bin/codex")
    run = lambda argv, **kw: calls.append((argv, kw))
    monkeypatch.setattr(codex_usage, "latest_reading", lambda sessions=None, **k: None)
    home = tmp_path / "site"
    home.mkdir()
    now = 1_000_000.0
    assert codex_usage.refresh(now, sessions=tmp_path, run=run, home=home) is True   # first probe runs
    assert codex_usage.refresh(now + 60.0, sessions=tmp_path, run=run, home=home) is False  # cooled down
    assert codex_usage.refresh(now + 60.0, sessions=tmp_path, run=run, home=home) is False
    assert len(calls) == 1
    # The cooldown expires: the next tick may probe again.
    assert codex_usage.refresh(now + codex_usage.PROBE_COOLDOWN_S + 1.0, sessions=tmp_path, run=run, home=home) is True
    assert len(calls) == 2


def test_a_probe_that_yields_a_reading_is_not_held_by_the_cooldown(tmp_path, monkeypatch):
    """A reading that did arrive must not be re-probed: the freshness rule alone decides."""
    calls = []
    monkeypatch.setattr(codex_usage.shutil, "which", lambda name: "/bin/codex")
    run = lambda argv, **kw: calls.append((argv, kw))
    now = 1_000_000.0
    readings = [{"at": now - 10_000.0, "rate_limits": {}}]   # old enough to probe
    monkeypatch.setattr(codex_usage, "latest_reading", lambda sessions=None, **k: readings[0])
    home = tmp_path / "site"
    home.mkdir()

    def probe(argv, **kw):
        calls.append((argv, kw))
        readings[0] = {"at": now, "rate_limits": {}}         # the probe produced a reading

    assert codex_usage.refresh(now, sessions=tmp_path, run=probe, home=home) is True
    assert codex_usage.refresh(now + 10.0, sessions=tmp_path, run=probe, home=home) is False  # reading is fresh
    assert len(calls) == 1


def test_a_successful_probe_does_not_hold_back_the_next_one(tmp_path, monkeypatch):
    """The cooldown is for probes that yielded nothing, not for a working account.

    A working codex-a must stay FRESH: a cooldown applied to every probe would leave the
    account STALE for half of every cycle and the router would skip it.
    """
    calls = []
    monkeypatch.setattr(codex_usage.shutil, "which", lambda name: "/bin/codex")
    run = lambda argv, **kw: calls.append((argv, kw))
    now = 1_000_000.0
    readings = [{"at": now - 10_000.0, "rate_limits": {}}]
    monkeypatch.setattr(codex_usage, "latest_reading", lambda sessions=None, **k: readings[0])
    home = tmp_path / "site"
    home.mkdir()

    def probe(argv, **kw):
        calls.append((argv, kw))
        readings[0] = {"at": now, "rate_limits": {}}         # a reading each time

    assert codex_usage.refresh(now, sessions=tmp_path, run=probe, home=home) is True
    # The reading is now stale again, so a probe is due. The successful probe must not
    # have started a cooldown that would block it.
    assert codex_usage.refresh(now + codex_usage.PROBE_AFTER_S + 1.0, sessions=tmp_path, run=probe, home=home) is True
    assert len(calls) == 2


def test_the_cooldown_state_survives_the_producer_process(tmp_path, monkeypatch):
    """The producer exits after each tick; the cooldown must not be process-local."""
    calls = []
    monkeypatch.setattr(codex_usage.shutil, "which", lambda name: "/bin/codex")
    run = lambda argv, **kw: calls.append((argv, kw))
    monkeypatch.setattr(codex_usage, "latest_reading", lambda sessions=None, **k: None)
    home = tmp_path / "site"
    home.mkdir()
    now = 1_000_000.0
    codex_usage.refresh(now, sessions=tmp_path, run=run, home=home)
    state = json.loads((home / codex_usage.PROBE_STATE).read_text())
    assert state["at"] == now and state["reading"] is False
    assert codex_usage.refresh(now + 5.0, sessions=tmp_path, run=run, home=home) is False
    assert len(calls) == 1


def test_a_failed_probe_still_starts_the_cooldown(tmp_path, monkeypatch):
    """A probe that raises must not be retried on every tick either."""
    calls = []

    def boom(argv, **kw):
        calls.append((argv, kw))
        raise OSError("codex is not installed")

    monkeypatch.setattr(codex_usage.shutil, "which", lambda name: "/bin/codex")
    monkeypatch.setattr(codex_usage, "latest_reading", lambda sessions=None, **k: None)
    home = tmp_path / "site"
    home.mkdir()
    now = 1_000_000.0
    assert codex_usage.refresh(now, sessions=tmp_path, run=boom, home=home) is False
    assert codex_usage.refresh(now + 5.0, sessions=tmp_path, run=boom, home=home) is False
    assert len(calls) == 1


def test_workspace_write_does_not_make_tmp_writable(tmp_path):
    cmd = engine_codex.command("gpt-6-luna", tmp_path)
    assert "sandbox_workspace_write.exclude_slash_tmp=true" in cmd
    assert "sandbox_workspace_write.exclude_tmpdir_env_var=true" in cmd
