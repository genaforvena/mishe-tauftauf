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

def test_wait_pattern_rejects_prose_mentions() -> None:
    assert omp_pane.wait_pattern("git commit -m 'reduce sleep in the CI follower'") is None
    assert omp_pane.wait_pattern("I am waiting on the reviewer follow-up") is None


def test_wait_pattern_rejects_prose_that_starts_with_a_command_word() -> None:
    # Line-start prose has no prompt and no command argument boundary, so it
    # must not excuse a still pane as a known wait.
    assert omp_pane.wait_pattern("sleep 150 is too long for CI") is None
    assert omp_pane.wait_pattern("tail -foo is not a wait") is None
    assert omp_pane.wait_pattern("gh run watch 38968299950 is yesterday's run") is None


def test_wait_pattern_matches_command_shape() -> None:
    assert omp_pane.wait_pattern("$ sleep 150") == "sleep"
    assert omp_pane.wait_pattern("$ tail -f /var/log/syslog") == "tail -f"
    assert omp_pane.wait_pattern("$ tail -f") == "tail -f"
    assert omp_pane.wait_pattern("$ gh run watch 38968299950") == "gh run watch"
    assert omp_pane.wait_pattern("$ gh run view 38242368632") == "gh run view"
    assert omp_pane.wait_pattern("Waiting for CI poll result") == "Waiting for CI poll result"


def test_ci_run_id_extracts_the_polled_run() -> None:
    assert omp_pane.ci_run_id("$ gh run view 38242368632 ⠋") == "38242368632"
    assert omp_pane.ci_run_id("$ sleep 150") is None


def _box(command: str, output: str = "") -> str:
    """A tool-call box as the omp TUI draws it."""
    width = 60
    lines = ["╭" + "─" * width + "╮", "│ " + command.ljust(width - 2) + "│"]
    if output:
        lines.append("├─── Output " + "─" * (width - 11) + "┤")
        lines.extend("│ " + row.ljust(width - 2) + "│" for row in output.splitlines())
    lines.append("╰" + "─" * width + "╯")
    return "\n".join(lines)


def test_wait_pattern_reads_the_command_the_tui_draws() -> None:
    # The TUI draws a tool call inside a box, so the prompt is never at
    # column 0.  A matcher that requires a column-0 prompt sees no wait at
    # all on this plant, and the sense reports every blocked pane as clean.
    assert omp_pane.wait_pattern(_box("$ sleep 150")) == "sleep"
    assert omp_pane.wait_pattern(_box("$ tail -f /var/log/syslog")) == "tail -f"
    assert omp_pane.wait_pattern(_box("$ gh run watch 38968299950")) == "gh run watch"
    assert omp_pane.wait_pattern(_box("$ gh run view 38242368632")) == "gh run view"


def test_wait_pattern_reads_a_compound_command_the_tui_draws() -> None:
    # The mind's real poll is a chain, and the wait is one segment of it.
    frame = _box("$ sleep 90 && rtk proxy cat checks.json | head -c 600; echo")
    assert omp_pane.wait_pattern(frame) == "sleep"
    frame = _box("$ systemctl --user status svc && sleep 6 && systemctl is-active svc")
    assert omp_pane.wait_pattern(frame) == "sleep"


def test_wait_pattern_reads_a_ci_poll_with_its_own_flags() -> None:
    # The plant's own CI read is `gh run view <id> --json status,conclusion`,
    # whose flag carries a value; the flag is part of the command, not a
    # reason to reject the command.
    frame = _box("$ gh run view 38242368632 --json status,conclusion,updatedAt")
    assert omp_pane.wait_pattern(frame) == "gh run view"
    assert omp_pane.ci_run_id(frame) == "38242368632"


def test_wait_pattern_ignores_a_tool_that_prints_a_command() -> None:
    # The tool's output is not the command the pane is running.
    frame = _box("$ cat notes.md", output="$ sleep 150\n$ gh run view 38242368632")
    assert omp_pane.wait_pattern(frame) is None
    assert omp_pane.ci_run_id(frame) is None


def test_wait_pattern_ignores_the_minds_own_prose() -> None:
    frame = "Now sleep 150 && rtk proxy cat ...\n" \
            "I am waiting on the reviewer follow-up\n" \
            "reduce sleep in the CI follower"
    assert omp_pane.wait_pattern(frame) is None


def test_ci_run_id_is_read_from_the_command_not_from_prose() -> None:
    frame = "the run was gh run view 38242368632 and it passed\n" \
            + _box("$ gh run watch 38968299950 --exit-status")
    assert omp_pane.ci_run_id(frame) == "38968299950"


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
