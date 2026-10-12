from __future__ import annotations

import os
from pathlib import Path

import pytest

from mishe_tauftauf import omp_pane


def test_command_line_reads_the_live_process_argv() -> None:
    argv = omp_pane.command_line(os.getpid())
    assert argv is not None
    assert argv and Path(argv[0]).name.startswith("python")


def test_command_line_unreadable_pid_is_none() -> None:
    # No such process: the file is absent, so this is "cannot read", not "".
    assert omp_pane.command_line(-1) is None


def test_option_reads_both_spellings() -> None:
    assert omp_pane.option(["omp", "--model", "a/b", "--cwd", "/x"], "--model") == "a/b"
    assert omp_pane.option(["omp", "--model=a/b"], "--model") == "a/b"


def test_option_missing_or_trailing_name_is_none() -> None:
    assert omp_pane.option(["omp", "--thinking", "high"], "--model") is None
    assert omp_pane.option(["omp", "--model"], "--model") is None


def test_model_absent_reads_none() -> None:
    assert omp_pane.model(["omp", "--cwd", "/x"]) is None


def test_model_without_a_usable_value_reads_none() -> None:
    # A malformed launcher: the flag last, an empty value, or another option as
    # the value. None is "cannot attribute", so the reader counts, not hides.
    assert omp_pane.model(["omp", "--model"]) is None
    assert omp_pane.model(["omp", "--model="]) is None
    assert omp_pane.model(["omp", "--model", "--thinking"]) is None


def test_same_model_matches_qualified_and_bare_names() -> None:
    assert omp_pane.same_model("opencode-go/longcat-2.5-preview-free",
                               "longcat-2.5-preview-free")
    assert omp_pane.same_model("a/b/model-x", "model-x")


def test_same_model_rejects_a_different_model() -> None:
    assert not omp_pane.same_model("muse-spark-1.3-contributor",
                                   "opencode-go/longcat-2.5-preview-free")


@pytest.mark.parametrize("raw,expected", [
    (b"omp\x00--model\x00a/b\x00", ["omp", "--model", "a/b"]),
    (b"omp\x00--model\x00a/b\x00\x00", ["omp", "--model", "a/b"]),
    (b"\xff\x00--model\x00x\x00", ["\ufffd", "--model", "x"]),
])
def test_fields_decode_nul_separated_bytes(monkeypatch,
                                           raw: bytes, expected: list[str]) -> None:
    monkeypatch.setattr(omp_pane.Path, "read_bytes", lambda self: raw)
    assert omp_pane.command_line(1) == expected


def test_normalize_frame_strips_volatile_chrome_but_keeps_tool_text() -> None:
    first = omp_pane.normalize_frame("$ gh run view 38242368632 ⠋ · 2m8s 85.8/s")
    second = omp_pane.normalize_frame("$ gh run view 38242368632 ⠙ · 2m23s 80.2/s")
    assert first == second == "$ gh run view 38242368632"


def test_normalize_frame_keeps_new_tool_output_visible() -> None:
    first = omp_pane.normalize_frame("$ gh run view 1 ⠋")
    second = omp_pane.normalize_frame("$ gh run view 1 ⠋\nresult: success")
    assert first != second


def test_wait_pattern_matches_allowlisted_polls_only() -> None:
    assert omp_pane.wait_pattern("$ gh run view 38242368632 ⠋ · 2m8s") == "gh run view"
    assert omp_pane.wait_pattern("$ sleep 150") == "sleep"
    assert omp_pane.wait_pattern("π > INSERT >") is None


def test_ci_run_id_extracts_the_polled_run() -> None:
    assert omp_pane.ci_run_id("$ gh run view 38242368632 ⠋") == "38242368632"
    assert omp_pane.ci_run_id("$ sleep 150") is None


def test_ci_run_state_decodes_status_and_rejects_junk() -> None:
    state = omp_pane.ci_run_state('{"status": "in_progress", "conclusion": "", "updatedAt": "2026-10-12T11:31:03Z"}')
    assert state == {"status": "in_progress", "conclusion": "",
                     "updated_at": "2026-10-12T11:31:03Z"}
    assert omp_pane.ci_run_state("not json") is None
    assert omp_pane.ci_run_state("[1, 2]") is None
    assert omp_pane.ci_run_state('{"status": ""}') is None
    assert omp_pane.ci_run_state('{"conclusion": "success"}') is None


def test_frame_empty_reports_whitespace_only_captures() -> None:
    assert omp_pane.frame_empty("")
    assert omp_pane.frame_empty("  \n  ")
    assert not omp_pane.frame_empty("$ sleep 150")
