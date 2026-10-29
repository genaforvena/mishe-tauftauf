"""The promotion gate must be wired into CI, or the hole it detects ships again.

A promoted release tree is cut from committed bytes.  When a committed module
imports a sibling that was never committed, the checkout still imports
cleanly -- the sibling's bytes sit untracked in the working tree -- and every
promoted runtime raises ``ModuleNotFoundError`` on that path.

The detector for this (``checks/promotion-import-closure.py``) existed and was
correct, but CI never ran it, so the pinned release ``401807ac`` was promoted
with a reachable hole: ``seed.py:627`` -> ``from .mind_loop import select``
while ``mind_loop.py`` was never committed.  Measured on this plant at
2026-10-29T15:18Z:

    $ python checks/promotion-import-closure.py --home . --repo .
    INCOMPLETE -- reachable local imports that do not resolve in this tree:
      mind_loop  <- seed.py:627 in loop()
    exit 2

A gate that nothing invokes is documentation, not a gate.  These tests assert
the wiring itself, so removing the CI step fails the suite rather than
silently re-opening the promotion hole.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
CHECK = REPO / "checks" / "promotion-import-closure.py"

CLI_REACHING = """
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
"""

SEED_WITH_BAD_IMPORT = """
def loop(home):
    from .mind_loop import select
    return select(home)
"""


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_runs_the_import_closure_gate():
    """CI must invoke the closure detector over the committed bytes.

    ``--repo`` is what makes this a promotion gate: it tests the committed
    bytes of HEAD, the bytes a release tree is cut from, rather than the
    working tree (whose untracked files would satisfy the imports and hide the
    hole).  ``--tree`` would test whatever tree was named, which is not a
    statement about what CI promotes.
    """
    text = _workflow_text()
    assert "checks/promotion-import-closure.py" in text, (
        "the promotion import-closure detector is not wired into CI")
    gate_lines = [line for line in text.splitlines()
                  if "checks/promotion-import-closure.py" in line and not line.strip().startswith("#")]
    assert gate_lines, "the closure gate must be an executed step, not a comment"
    gate = " ".join(gate_lines)
    assert "--repo" in gate, (
        "the closure gate must test the committed bytes (--repo), not the "
        "working tree, whose untracked files would satisfy the import")


def test_ci_gate_runs_before_the_test_suite():
    """The gate must be able to fail the build before anything promotes it."""
    lines = [line.strip() for line in _workflow_text().splitlines()]
    gate_index = next(i for i, line in enumerate(lines)
                      if "checks/promotion-import-closure.py" in line and not line.startswith("#"))
    pytest_index = next(i for i, line in enumerate(lines)
                         if line.startswith("- run: python -m pytest -q"))
    assert gate_index < pytest_index, (
        "the closure gate must run before the suite so a promotion hole fails "
        "the build itself")


def test_the_gate_rejects_a_tree_with_a_reachable_hole(tmp_path):
    """The gate must fail, not warn, when a reachable import does not resolve."""
    pkg = tmp_path / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text(CLI_REACHING, encoding="utf-8")
    (pkg / "seed.py").write_text(SEED_WITH_BAD_IMPORT, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(REPO), "--tree", str(tmp_path)],
        capture_output=True, text=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "mind_loop" in result.stdout


def test_the_gate_accepts_a_tree_whose_imports_resolve(tmp_path):
    """A complete tree must pass, or the gate cannot be a gate."""
    pkg = tmp_path / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text(CLI_REACHING, encoding="utf-8")
    (pkg / "seed.py").write_text(SEED_WITH_BAD_IMPORT, encoding="utf-8")
    (pkg / "mind_loop.py").write_text("def select(home):\n    return home\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHECK), "--home", str(REPO), "--tree", str(tmp_path)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
