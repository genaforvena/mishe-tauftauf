"""wakes-view-engines: the newest output of each engine model renders readably; a failure is RED, never silent."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("wakes_view_engines", REPO / "checks" / "wakes-view-engines.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def omp_lines(model, *events):
    out = []
    for kind, body in events:
        out.append(json.dumps({"type": kind, **body}))
    return "\n".join(out) + "\n"


def clean_events(model):
    return [
        ("agent_start", {}),
        ("turn_start", {}),
        ("message_end", {"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
                                     "model": model, "stopReason": "stop"}}),
        ("turn_end", {}),
        ("agent_end", {}),
    ]


def write_out(wakes: Path, key: str, model: str, body: str, suffix=".prompt.out.jsonl"):
    path = wakes / f"{key}{suffix}"
    path.write_text(body, encoding="utf-8")
    return path


def site(tmp_path, models):
    """A site whose engines use `models` (role -> model) and whose wakes/ hold one output per model."""
    home = tmp_path / "site"
    (home / "wakes").mkdir(parents=True)
    (home / "engines").mkdir()
    for role, model in models.items():
        (home / "engines" / f"{role}.json").write_text(json.dumps({"engine": "omp", "model": model}))
    return home


def test_clean_output_passes_in_both_modes(tmp_path):
    home = site(tmp_path, {"genome": "gpt-6.1-terra"})
    body = omp_lines("gpt-6.1-terra", *clean_events("gpt-6.1-terra"))
    write_out(home / "wakes", "wake-genome-1", "gpt-6.1-terra", body)
    code, lines = check.evaluate(home, REPO, 50, False)
    assert code == 0, lines


def test_unknown_event_for_known_type_fails(tmp_path):
    problems = check.lint('UNKNOWN event message_end; raw: {"type":"message_end"}\n')
    assert any("UNKNOWN event for known type message_end" in p for p in problems)


def test_unknown_event_for_new_type_is_not_a_failure(tmp_path):
    assert check.lint('UNKNOWN event brand_new_kind; raw: {"type":"brand_new_kind"}\n') == []


def test_unknown_stream_record_for_known_type_fails():
    problems = check.lint('UNKNOWN stream record: event envelope missing; raw: {"type":"turn_end","x":1}')
    assert any("UNKNOWN stream record for known type turn_end" in p for p in problems)


def test_bookkeeping_line_fails():
    assert any("bookkeeping" in p for p in check.lint("[assistant] hi\nturn_start\n"))
    assert any("bookkeeping" in p for p in check.lint("UNKNOWN event message_start; raw: x"))


def test_consecutive_duplicate_tool_output_fails_and_distinct_outputs_pass():
    dup = "[tool output] listing\nfile-a\nfile-b\n[tool output] listing\nfile-a\nfile-b\n"
    assert any("consecutive duplicate tool output" in p for p in check.lint(dup))
    distinct = "[tool output] one\nx\n[tool output] two\ny\n"
    assert check.lint(distinct) == []


def test_raw_envelope_and_json_lines_fail_but_tool_content_does_not():
    leaked = '{"type":"message_end","message":{}}'
    assert any("raw JSON object line" in p for p in check.lint(leaked))
    envelope = '"assistantMessageEvent": {"type": "text_delta"}'
    assert any("raw event envelope key" in p for p in check.lint(envelope))
    # The same text printed by a command is content the mind read, not a renderer leak.
    quoted = "[tool output] sed of the renderer\n" + envelope + "\n"
    assert check.lint(quoted) == []


def test_renderer_exception_is_a_violation(tmp_path):
    def boom(path, limit=0, final=False):
        raise KeyError("missing field")

    problems, key = check.render_problems(tmp_path / "wake-x-1.prompt.out.jsonl", lambda t: [], boom)
    assert key == "wake-x-1"
    assert problems and all("render exception" in p for p in problems)


def test_failing_render_exits_two_with_sample_key(tmp_path):
    home = site(tmp_path, {"genome": "gpt-6.1-terra"})
    # A known event whose payload is malformed: the renderer reports it as an UNKNOWN stream record.
    body = ('{"type":"message_end","message":"not-an-object","model":"gpt-6.1-terra"}\n')
    write_out(home / "wakes", "wake-genome-2", "gpt-6.1-terra", body)
    code, lines = check.evaluate(home, REPO, 50, False)
    assert code == 2
    assert any("sample=wake-genome-2" in line for line in lines)


def test_configured_model_without_output_is_unknown(tmp_path):
    home = site(tmp_path, {"genome": "gpt-6.1-terra", "senses": "claude-haiku-5-5"})
    write_out(home / "wakes", "wake-genome-3", "gpt-6.1-terra", omp_lines("gpt-6.1-terra", *clean_events("x")))
    code, lines = check.evaluate(home, REPO, 50, False)
    assert code == 3
    assert any("role senses: model claude-haiku-5-5" in line and "UNKNOWN" in line for line in lines)


def test_no_outputs_is_undecidable(tmp_path):
    home = site(tmp_path, {"genome": "gpt-6.1-terra"})
    code, _ = check.evaluate(home, REPO, 50, False)
    assert code == 3


def test_model_name_read_from_claude_envelope(tmp_path):
    path = tmp_path / "wake-senses-9.prompt.out.json"
    path.write_text(json.dumps({"result": "ok", "modelUsage": {"claude-haiku-5-5": {}}}))
    assert check.model_name(path) == "claude-haiku-5-5"


def test_route_sends_once_per_distinct_failure(tmp_path, monkeypatch):
    home = tmp_path / "site"
    (home / "bin").mkdir(parents=True)
    cli = home / "bin" / "mishe-tauftauf"
    sent = tmp_path / "sent.txt"
    cli.write_text(f'#!/bin/sh\ncat >> "{sent}"\necho "--- end" >> "{sent}"\n')
    cli.chmod(0o755)
    failures = [("gpt-6.1-terra", "wake-genome-2", "UNKNOWN event for known type turn_end")]
    check.route(home, failures)
    check.route(home, failures)
    assert sent.read_text().count("[dm] to=genome") == 1
    check.route(home, [("gpt-6.1-terra", "wake-genome-5", "UNKNOWN event for known type turn_end")])
    assert sent.read_text().count("[dm] to=genome") == 2
