"""checks/repo-root-strays.py: a repo-root entry named with an unexpanded shell literal is RED.

Incident it observes (2026-11-15): a directory named ``$MISHE_SEED_HOME`` appeared in the repo
root holding another role's wall, so that role's real site wall stayed stale and nothing said so.

Second shape (2026-10-15 and 2026-10-19): a hand-typed ``.mishe-tauftauft`` entry, which carries
no ``$`` at all and which ``.gitignore`` (``.mishe-*``) hides from Git, so neither ``git status``
nor the branch-inventory ``strays`` field named it.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "checks" / "repo-root-strays.py"

spec = importlib.util.spec_from_file_location("repo_root_strays", CHECK)
strays_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strays_mod)


def _site_home(tmp_path: Path) -> Path:
    home = tmp_path / ".mishe-tauftauf"
    home.mkdir()
    return home


def test_flags_a_literal_seed_home_tree(tmp_path):
    (tmp_path / "$MISHE_SEED_HOME" / "walls").mkdir(parents=True)
    code, lines = strays_mod.strays(tmp_path)
    assert code == 2
    assert any("$MISHE_SEED_HOME" in line for line in lines)


def test_flags_a_literal_seed_home_tree_when_home_is_given(tmp_path):
    """The routed reading passes the site home; the ``$`` shape must still fire."""
    _site_home(tmp_path)
    (tmp_path / "$MISHE_SEED_HOME" / "walls").mkdir(parents=True)
    code, lines = strays_mod.strays(tmp_path, tmp_path / ".mishe-tauftauf")
    assert code == 2
    assert any("$MISHE_SEED_HOME" in line for line in lines)


def test_flags_a_misspelled_site_home_entry(tmp_path):
    """A hand-typed ``.mishe-tauftauft`` carries no ``$``, so the prefix rule is what sees it."""
    _site_home(tmp_path)
    (tmp_path / ".mishe-tauftauft-notes.txt").write_text("x")
    code, lines = strays_mod.strays(tmp_path, tmp_path / ".mishe-tauftauf")
    assert code == 2
    assert any(".mishe-tauftauft-notes.txt" in line for line in lines)
    assert any("site-home literal" in line for line in lines)


def test_accepts_the_real_site_home_itself(tmp_path):
    """The plant's own home is the one ``.mishe-*`` entry that belongs at a checkout root."""
    home = _site_home(tmp_path)
    code, lines = strays_mod.strays(tmp_path, home)
    assert code == 0
    assert lines == ["no site-home literal among 1 repo-root entries"]


def test_prefix_rule_needs_a_home(tmp_path):
    """Without a site home the check keeps its narrow contract instead of guessing one."""
    (tmp_path / ".mishe-tauftauft-notes.txt").write_text("x")
    code, lines = strays_mod.strays(tmp_path)
    assert code == 0
    assert lines == ["no unexpanded path literal among 1 repo-root entries"]


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


def test_cli_passes_the_resolved_home_and_repo(tmp_path):
    """The observer runner always passes both; the prefix rule is live, not opt-in."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    home = repo / ".mishe-tauftauf"
    home.mkdir()
    (repo / ".mishe-tauftauft-notes.txt").write_text("x")
    result = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(home), "--repo", str(repo)],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert ".mishe-tauftauft-notes.txt" in result.stdout
    assert "site-home literal" in result.stdout
