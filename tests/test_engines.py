import os
import sys
import time
from pathlib import Path

import pytest

from mishe_tauftauf import engine_claude, engine_codex, engine_omp, engine_qwen, engine_process as proc, engines, wake


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
    (tmp_path / "engines" / "genome.json").write_text('{"engine":"qwen","model":"x"}')
    assert engines.for_role(tmp_path, "genome").name == "qwen"
    (tmp_path / "engines" / "genome.json").write_text('{"engine":"bogus"}')
    with pytest.raises(ValueError):
        engines.for_role(tmp_path, "genome")


def test_a_codex_role_without_a_model_gets_a_usable_model(tmp_path):
    """A config of {"engine":"codex"} used to build a ``-m None`` argv.

    ``subprocess.Popen`` raises TypeError for it (not OSError), so the crash escaped the
    wake instead of being recorded as a failure: review finding on release bcd536a3.
    """
    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "p.json").write_text('{"engine":"codex"}')
    engine = engines.for_role(tmp_path, "p")
    assert engine.name == "codex"
    assert engine.model == engines.DEFAULT_CODEX_MODEL
    argv = engine_codex.command(engine.model, tmp_path)
    assert argv[argv.index("-m") + 1] == engines.DEFAULT_CODEX_MODEL
    assert all(isinstance(part, str) for part in argv)


def test_claude_tools_can_be_narrowed_per_role(tmp_path):
    assert engine_claude.command("m", tools=("Bash(mishe yield:*)",))[-1] != "Bash(mishe yield:*)"
    cmd = engine_claude.command("m", tools=("Bash(mishe yield:*)",))
    allowed = cmd[cmd.index("--allowedTools") + 1]
    assert allowed == "Bash(mishe yield:*)" and "Read" not in cmd
    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "p.json").write_text('{"engine":"claude","tools":["Read"]}')
    assert engines.for_role(tmp_path, "p").tools == ("Read",)


def test_omp_streams_events_before_completion(tmp_path, monkeypatch):
    seen = {}
    def run(command, **kw):
        seen.update(command=command, **kw)
        return 0
    monkeypatch.setattr(proc, "run_bounded", run)
    prompt = tmp_path / "wake.prompt.md"
    assert engine_omp.OmpEngine("m").run(prompt, env={}, cwd=tmp_path, timeout=10) == 0
    assert seen["command"][seen["command"].index("--mode") + 1] == "json"
    assert seen["stdout_to"] == tmp_path / "wake.prompt.out.jsonl"


def test_omp_wakes_bound_thinking_and_honour_a_configured_level(tmp_path):
    cmd = engine_omp.command("m", tmp_path, tmp_path / "p.md")
    assert cmd[cmd.index("--thinking") + 1] == "low"
    cmd = engine_omp.command("m", tmp_path, tmp_path / "p.md", "minimal")
    assert cmd[cmd.index("--thinking") + 1] == "minimal"
    cmd = engine_omp.command("m", tmp_path, tmp_path / "p.md", "bogus")
    assert cmd[cmd.index("--thinking") + 1] == "low"


def test_qwen_command_is_headless_bounded_and_unpersisted():
    cmd = engine_qwen.command("qmodel_latest")
    assert cmd[:2] == ["qwen", "-p"]
    assert "--no-session-persistence" in cmd and "--permission-mode" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dont_ask"
    assert cmd[cmd.index("--model") + 1] == "qmodel_latest"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "Bash" in cmd and "Read" in cmd


def test_a_qwen_role_without_a_model_gets_a_usable_model(tmp_path):
    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "p.json").write_text('{"engine":"qwen"}')
    engine = engines.for_role(tmp_path, "p")
    assert engine.name == "qwen"
    assert engine.model == engines.DEFAULT_QWEN_MODEL
    argv = engine_qwen.command(engine.model, engine.tools)
    assert argv[argv.index("--model") + 1] == engines.DEFAULT_QWEN_MODEL
    assert all(isinstance(part, str) for part in argv)


def test_qwen_tools_can_be_narrowed_per_role(tmp_path):
    (tmp_path / "engines").mkdir()
    (tmp_path / "engines" / "p.json").write_text('{"engine":"qwen","tools":["Read"]}')
    engine = engines.for_role(tmp_path, "p")
    assert engine.tools == ("Read",)
    cmd = engine_qwen.command(engine.model, engine.tools)
    assert cmd.count("--allowed-tools") == 1
    assert cmd[cmd.index("--allowed-tools") + 1] == "Read" and "Bash" not in cmd


def test_qwen_wake_retains_the_claude_shaped_envelope(tmp_path, monkeypatch):
    seen = {}
    def run(command, **kw):
        seen.update(command=command, **kw)
        return 0
    monkeypatch.setattr(proc, "run_bounded", run)
    prompt = tmp_path / "wake.prompt.md"
    assert engine_qwen.QwenEngine("qmodel_latest").run(prompt, env={}, cwd=tmp_path, timeout=10) == 0
    assert seen["stdin_from"] == prompt
    assert seen["stdout_to"] == tmp_path / "wake.prompt.out.json"
    assert seen["stderr_to"] == tmp_path / "wake.prompt.err"


def test_stderr_to_keeps_the_stdout_document_clean(tmp_path):
    out, err = tmp_path / "out.json", tmp_path / "err"
    code = proc.run_bounded(
        [sys.executable, "-c", "import sys; sys.stdout.write('{\"a\":1}'); sys.stderr.write('noise')"],
        env=dict(os.environ), cwd=tmp_path, timeout=10, stdout_to=out, stderr_to=err)
    assert code == 0
    assert out.read_text() == '{"a":1}'
    assert err.read_text() == "noise"


def test_stderr_joins_stdout_by_default(tmp_path):
    out = tmp_path / "out"
    code = proc.run_bounded(
        [sys.executable, "-c", "import sys; sys.stdout.write('a'); sys.stderr.write('b')"],
        env=dict(os.environ), cwd=tmp_path, timeout=10, stdout_to=out)
    assert code == 0
    assert out.read_text() == "ab"
