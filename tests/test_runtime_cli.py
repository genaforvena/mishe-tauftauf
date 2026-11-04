"""End to end through the CLI with a fake `claude` binary on PATH."""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mishe_tauftauf import intentions, owed, runtime_cli, signal_store as store

FAKE_CLAUDE = """#!/bin/sh
cat > "$MISHE_SEED_HOME/engine-saw-prompt.txt"
echo '{"usage":{"input_tokens":1,"output_tokens":1}}'
if [ -z "$FAKE_NO_YIELD" ]; then
  echo "new wall from fake" | "$PYBIN" -m mishe_tauftauf yield --result changed --again 30m
fi
"""


@pytest.fixture
def site(tmp_path, monkeypatch):
    home = tmp_path / "site"
    (home / "charters").mkdir(parents=True)
    (home / "charters" / "genome.md").write_text("I am genome.")
    (home / "walls").mkdir()
    (home / "walls" / "genome.md").write_text("OLD WALL")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    claude = bindir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("PYBIN", sys.executable)
    monkeypatch.setenv("PYTHONPATH", str(Path(runtime_cli.__file__).parents[1]))
    monkeypatch.setenv("MISHE_SEED_HOME", str(home))
    monkeypatch.delenv("MISHE_ROLE", raising=False)
    return home


def cli(*argv):
    return runtime_cli.main(list(argv))


def test_signal_register_observe_and_inbox(site, capsys):
    assert cli("signal", "register", "ci", "--max-age", "60", "--subscribers", "genome") == 0
    assert cli("signal", "observe", "ci", "RED", "--source", "t") == 0
    assert owed.check(site, "genome", now=__import__("time").time()).reasons == ["signal:ci"]


def test_wake_with_nothing_owed_does_not_start_the_engine(site):
    assert cli("wake", "genome") == 0
    assert not (site / "engine-saw-prompt.txt").exists()


def test_wake_runs_claude_headless_and_yield_lands(site):
    owed.touch_inbox(site, "genome")
    assert cli("wake", "genome") == 0
    assert "I am genome." in (site / "engine-saw-prompt.txt").read_text()
    assert (site / "walls" / "genome.md").read_text().startswith("new wall from fake")
    assert "again" in [i["id"] for i in intentions.list_(site, "genome")]
    assert owed.check(site, "genome", now=__import__("time").time()).reasons == []


def test_wake_without_yield_exits_nonzero_and_leaves_unknown(site, monkeypatch):
    monkeypatch.setenv("FAKE_NO_YIELD", "1")
    owed.touch_inbox(site, "genome")
    assert cli("wake", "genome") == 3


def test_hold_blocks_wake(site):
    owed.touch_inbox(site, "genome")
    assert cli("hold", "on", "--reason", "owner") == 0
    assert cli("wake", "genome") == 0 and not (site / "engine-saw-prompt.txt").exists()
    assert cli("hold", "off") == 0


def test_yield_outside_a_wake_is_refused(site):
    assert cli("yield", "--result", "changed") == 2
