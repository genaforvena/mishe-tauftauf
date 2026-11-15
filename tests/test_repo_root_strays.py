"""checks/repo-root-strays.py: a repo-root entry named with an unexpanded shell literal is RED.

Incident it observes (2026-11-15): a directory named ``$MISHE_SEED_HOME`` appeared in the repo
root holding another role's wall, so that role's real site wall stayed stale and nothing said so.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "checks" / "repo-root-strays.py"

spec = importlib.util.spec_from_file_location("repo_root_strays", CHECK)
strays_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strays_mod)


def test_flags_a_literal_seed_home_tree(tmp_path):
    (tmp_path / "$MISHE_SEED_HOME" / "walls").mkdir(parents=True)
    code, lines = strays_mod.strays(tmp_path)
    assert code == 2
    assert any("$MISHE_SEED_HOME" in line for line in lines)


def test_holds_for_an_ordinary_checkout(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "checks").mkdir()
    (tmp_path / "README.md").write_text("x")
    code, lines = strays_mod.strays(tmp_path)
    assert code == 0
    assert lines == ["no unexpanded path literal among 3 repo-root entries"]


def test_undecidable_when_the_root_cannot_be_listed(tmp_path):
    missing = tmp_path / "absent"
    code, lines = strays_mod.strays(missing)
    assert code == 3
    assert "unreadable" in lines[0]


def test_cli_exit_code_is_the_verdict(tmp_path):
    (tmp_path / "$MISHE_SEED_HOME").mkdir()
    done = subprocess.run([sys.executable, str(CHECK), "--repo", str(tmp_path)],
                          capture_output=True, text=True)
    assert done.returncode == 2
    assert "$MISHE_SEED_HOME" in done.stdout
