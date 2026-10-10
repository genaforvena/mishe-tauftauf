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
