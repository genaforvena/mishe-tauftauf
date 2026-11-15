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
