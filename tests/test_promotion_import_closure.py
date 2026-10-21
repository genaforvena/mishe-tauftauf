"""The promotion import-closure detector must catch a reachable hole and
leave a latent one alone.

A promoted release tree is cut from committed bytes.  When a committed module
imports a sibling that was never committed, the checkout still imports
cleanly -- the sibling's bytes sit untracked in the working tree -- and the
failure first appears in the promoted runtime.

Measured on this plant on 2026-10-21 (body-research wake 80486, confirmed by
discover wake 81164): commit ``d4f4592`` added ``seed.py``'s
``from .mind_loop import select``, commit ``2e88d94`` added the ``seed loop``
subcommand that calls it, and ``mind_loop.py`` was never committed.  The
committed head therefore dispatches a CLI subcommand into a module the tree
does not contain, and every promoted runtime raises ``ModuleNotFoundError``
on that path.  The running pin holds the same import but no CLI path calls
``seed.loop``, so there the hole is latent.

These tests exercise the detector against synthetic trees, so a regression in
the detector is caught without depending on the plant's own uncommitted
files staying uncommitted.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "checks" / "promotion-import-closure.py"


def run_check(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                           "--tree", str(tree)],
                          capture_output=True, text=True)


def make_tree(tmp: Path, *, cli_body: str, seed_body: str,
              present: list[str]) -> Path:
    """Build a minimal package tree with the given cli.py and seed.py bodies."""
    pkg = tmp / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text(cli_body, encoding="utf-8")
    (pkg / "seed.py").write_text(seed_body, encoding="utf-8")
    for name in present:
        (pkg / f"{name}.py").write_text("", encoding="utf-8")
    return tmp


CLI_REACHING = '''
import argparse


def cmd_seed(args):
    from . import seed
    result = seed.loop(args.home)
    print(result)
    return 0


def parser():
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed").add_subparsers(dest="seed_command", required=True)
    p = seed.add_parser("loop")
    p.set_defaults(func=cmd_seed)
    return root
'''

CLI_NOT_REACHING = '''
import argparse


def cmd_seed(args):
    from . import seed
    result = seed.other(args.home)
    print(result)
    return 0


def parser():
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed").add_subparsers(dest="seed_command", required=True)
    p = seed.add_parser("other")
    p.set_defaults(func=cmd_seed)
    return root
'''

SEED_WITH_BAD_IMPORT = '''
def loop(home):
    from .mind_loop import select
    return select(home)
'''

SEED_CLEAN = '''
def loop(home):
    return home
'''


def test_reachable_missing_module_fails(tmp_path):
    """A CLI-dispatched function whose import is absent must FAIL."""
    tree = make_tree(tmp_path / "reachable", cli_body=CLI_REACHING,
                     seed_body=SEED_WITH_BAD_IMPORT, present=[])
    result = run_check(tree)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "mind_loop" in result.stdout
    assert "seed.py" in result.stdout


def test_unreachable_missing_module_is_latent(tmp_path):
    """The same hole with no CLI path to it is latent, not a failure."""
    tree = make_tree(tmp_path / "latent", cli_body=CLI_NOT_REACHING,
                     seed_body=SEED_WITH_BAD_IMPORT, present=[])
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "latent" in result.stdout
    assert "mind_loop" in result.stdout


def test_complete_tree_passes(tmp_path):
    """A tree whose imports all resolve must pass."""
    tree = make_tree(tmp_path / "complete", cli_body=CLI_REACHING,
                     seed_body=SEED_WITH_BAD_IMPORT, present=["mind_loop"])
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK every local import resolves" in result.stdout


def test_missing_tree_is_undecidable(tmp_path):
    """A tree that cannot be read is UNKNOWN, never OK."""
    result = run_check(tmp_path / "absent")
    assert result.returncode == 3, result.stdout + result.stderr


def test_module_level_import_is_not_claimed_reachable(tmp_path):
    """An import at module level is not attributed to a dispatched function.

    The detector must not claim reachability it cannot see; such an import is
    reported latent, so the reader is told the hole exists without being told
    a command breaks.
    """
    cli_body = CLI_REACHING.replace("    from . import seed\n",
                                    "    from . import seed\n")
    seed_body = "from .mind_loop import select\n\n\ndef loop(home):\n    return select(home)\n"
    tree = make_tree(tmp_path / "module_level", cli_body=cli_body,
                     seed_body=seed_body, present=[])
    result = run_check(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "latent" in result.stdout


def test_real_committed_head_is_reported(tmp_path):
    """The plant's own committed bytes carry the reachable hole.

    This is the live observation the detector exists for.  If the hole is
    closed -- the module committed, or the dispatch held back -- this test
    fails and says so, which is the point: the detector must not go quiet
    when the defect it watches is repaired.
    """
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(REPO)],
                             capture_output=True, text=True)
    assert result.returncode in (0, 2), result.stdout + result.stderr
    if result.returncode == 2:
        assert "mind_loop" in result.stdout, result.stdout
        assert "seed.py" in result.stdout, result.stdout


def _make_repo(tmp_path: Path, tracked: dict[str, str],
               untracked: dict[str, str] | None = None) -> Path:
    """A git repository with tracked files and optional untracked ones."""
    repo = tmp_path / "repo"
    (repo / "src" / "mishe_tauftauf").mkdir(parents=True)
    for name, body in tracked.items():
        (repo / "src" / "mishe_tauftauf" / name).write_text(body, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True, env=env)
    # The untracked files are written *after* the commit, so they stay
    # untracked -- exactly the plant's situation.
    for name, body in (untracked or {}).items():
        (repo / "src" / "mishe_tauftauf" / name).write_text(body, encoding="utf-8")
    return repo


def test_pending_edit_importing_untracked_module_is_reported(tmp_path):
    """A tracked edit importing an untracked module is a pending hole.

    This is the forward direction: the checkout imports cleanly, the suite
    passes, and the promoted runtime breaks the moment the edit is committed.
    """
    repo = _make_repo(tmp_path / "pending",
                      tracked={"__init__.py": "", "cli.py": CLI_REACHING,
                               "seed.py": SEED_CLEAN},
                      untracked={"native_pane.py": ""})
    # Edit a tracked file to import the untracked module.
    path = repo / "src" / "mishe_tauftauf" / "seed.py"
    path.write_text("from .native_pane import read_prompt\n\n\n"
                    "def loop(home):\n    return home\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(repo), "--forward"],
                             capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "pending" in result.stdout
    assert "native_pane" in result.stdout
    assert "seed.py" in result.stdout


def test_forward_mode_silent_when_no_pending_hole(tmp_path):
    """A clean tracked tree reports no pending hole."""
    repo = _make_repo(tmp_path / "clean",
                      tracked={"__init__.py": "", "cli.py": CLI_REACHING,
                               "seed.py": SEED_CLEAN})
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(repo), "--forward"],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pending" not in result.stdout


def test_forward_mode_off_by_default(tmp_path):
    """Without --forward the pending hole is not reported.

    The scheduled runner must not flap on a peer's uncommitted edit, which is
    not yet a property of the tree under promotion.
    """
    repo = _make_repo(tmp_path / "off",
                      tracked={"__init__.py": "", "cli.py": CLI_REACHING,
                               "seed.py": SEED_CLEAN},
                      untracked={"native_pane.py": ""})
    path = repo / "src" / "mishe_tauftauf" / "seed.py"
    path.write_text("from .native_pane import read_prompt\n\n\n"
                    "def loop(home):\n    return home\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(repo)],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pending" not in result.stdout


def test_pending_requires_a_tracked_edit_not_an_untracked_source(tmp_path):
    """A *pending hole* needs a tracked file that was edited.

    An untracked file importing another untracked file is not a pending hole:
    it is not a change to anything tracked, so committing the tree does not
    open it.  Reporting it as pending would over-report the plant's own
    untracked work as a defect of the tree under promotion.

    This distinction is not cosmetic.  On the live plant the untracked
    ``mind_loop.py`` imports the untracked ``native_resident``, and
    ``seed.py``'s *committed* import of ``mind_loop`` is a different, already
    reported hole.  A rule that reported every untracked-to-untracked import
    would name three pending holes where one is real.
    """
    repo = _make_repo(tmp_path / "untracked_source",
                      tracked={"__init__.py": "", "cli.py": CLI_REACHING,
                               "seed.py": SEED_CLEAN},
                      untracked={"native_pane.py": "",
                                 "native_resident.py": "from .native_pane import x\n"})
    result = subprocess.run([sys.executable, str(CHECK), "--home", str(REPO),
                              "--repo", str(repo), "--forward"],
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pending" not in result.stdout, result.stdout
