import os
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import engine_claude, engine_omp, engine_process as proc, engines, wake


def test_missing_binary_is_not_started():
    with pytest.raises(wake.EngineNotStarted):
        proc.run_bounded(["/nonexistent/engine-binary"], env={}, cwd=Path("."), timeout=5)


def test_exit_code_is_returned(tmp_path):
    out = tmp_path / "out"
    code = proc.run_bounded([sys.executable, "-c", "import sys; sys.exit(3)"],
                            env=dict(os.environ), cwd=tmp_path, timeout=10, stdout_to=out)
    assert code == 3


def test_timeout_kills_the_whole_process_group(tmp_path):
    marker = tmp_path / "child-alive"
    child = tmp_path / "child.py"
    child.write_text(f"import time, pathlib\ntime.sleep(1.5)\npathlib.Path({str(marker)!r}).write_text('x')\n")
    parent = tmp_path / "parent.py"
    parent.write_text(f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {str(child)!r}])\ntime.sleep(60)\n")
    with pytest.raises(wake.EngineTimeout):
        proc.run_bounded([sys.executable, str(parent)], env=dict(os.environ), cwd=tmp_path,
                         timeout=0.5, stdout_to=tmp_path / "out")
    time.sleep(2.0)
    assert not marker.exists()          # grandchild died with the group


def test_claude_command_is_headless_bounded_and_unpersisted(tmp_path):
    cmd = engine_claude.command("claude-haiku-5-5")
    assert cmd[:2] == ["claude", "-p"]
    assert "--no-session-persistence" in cmd and "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert "Bash" in cmd and "Read" in cmd


def test_omp_command_is_print_no_session(tmp_path):
    cmd = engine_omp.command("m", tmp_path, tmp_path / "p.md")
    assert cmd[1:4] == ["--print", "--no-session", "--no-extensions"]
    assert cmd[-1] == f"@{tmp_path / 'p.md'}"


def test_engine_choice_defaults_and_rejects_unsupported(tmp_path):
    assert engines.for_role(tmp_path, "genome").name == "claude"
    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "genome.json").write_text('{"engine":"omp","model":"x"}')
    assert engines.for_role(tmp_path, "genome").name == "omp"
    (tmp_path / "engines" / "genome.json").write_text('{"engine":"codex"}')
    with pytest.raises(ValueError):
        engines.for_role(tmp_path, "genome")


def test_claude_tools_can_be_narrowed_per_role(tmp_path):
    assert engine_claude.command("m", tools=("Bash(mishe yield:*)",))[-1] != "Bash(mishe yield:*)"
    cmd = engine_claude.command("m", tools=("Bash(mishe yield:*)",))
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert allowed == "Bash(mishe yield:*)" and "Read" not in cmd
    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "p.json").write_text('{"engine":"claude","tools":["Read"]}')
    assert engines.for_role(tmp_path, "p").tools == ("Read",)
