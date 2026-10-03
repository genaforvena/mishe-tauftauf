"""The import-root guard ships, and is total, so a test run cannot silently
describe a different copy of the package.

The seed launcher exports ``PYTHONPATH=<pinned release>/src`` into every role
pane, so a bare ``pytest`` in the checkout imports the pinned release rather
than HEAD. Before this guard was tracked, the same run reported failures for
helpers a checkout test names but the release predates — an ``AttributeError``
that reads as a broken change rather than a stale import. These tests pin both
directions: the guard refuses a foreign root, refuses silence, and allows HEAD.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CHECKOUT_ROOT = Path(__file__).resolve().parent.parent
_CONFTEST = _CHECKOUT_ROOT / "conftest.py"

# Loads the guard as a module from a copy of the tree under test. The conftest
# runs at import time, so exit status alone separates refuse from allow.
_PROBE = (
    "import importlib.util, sys;"
    "spec = importlib.util.spec_from_file_location('_guard', sys.argv[1]);"
    "mod = importlib.util.module_from_spec(spec);"
    "spec.loader.exec_module(mod)")

# The venv carries an installed copy of the package on ``sys.path`` that no
# ``PYTHONPATH`` clears, so a probe asking "what if nothing resolves" must drop
# the venv's own entries and leave only the stdlib before the guard runs.
_PROBE_ISOLATED = (
    "import importlib.util, sys, sysconfig;"
    "stdlib = sysconfig.get_paths()['stdlib'];"
    "sys.path[:] = [p for p in sys.path if p.startswith(stdlib)];"
    "spec = importlib.util.spec_from_file_location('_guard', sys.argv[1]);"
    "mod = importlib.util.module_from_spec(spec);"
    "spec.loader.exec_module(mod)")


def _probe(pythonpath: str, conftest: Path, cwd: str = ".",
           isolated: bool = False) -> subprocess.CompletedProcess[str]:
    """Load the guard with ``PYTHONPATH`` naming the only source root to test.

    ``PYTHONPATH`` is replaced rather than extended, and ``cwd`` is raised above
    the checkout, so the probe cannot inherit this tree or the venv's installed
    copy and answers only the root it is handed.
    """
    environment = {"PYTHONPATH": pythonpath, "PATH": "/usr/bin:/bin",
                   "HOME": str(Path.home())}
    source = _PROBE_ISOLATED if isolated else _PROBE
    return subprocess.run([sys.executable, "-c", source, str(conftest)],
                          capture_output=True, text=True, env=environment,
                          cwd=cwd, timeout=60)


def test_the_guard_is_tracked_so_a_release_carries_it() -> None:
    """An untracked conftest.py protects only its own checkout.

    A release worktree is a clean checkout of a commit, so the guard reaches a
    pinned release only if the path is tracked.
    """
    listed = subprocess.run(
        ["git", "-C", str(_CHECKOUT_ROOT), "ls-files", "--error-unmatch",
         "conftest.py"],
        capture_output=True, text=True, timeout=60)
    assert listed.returncode == 0, (
        "conftest.py is untracked, so no release carries the import-root guard: "
        "a checkout test run against a pinned release falls through to the "
        "release's own package and reports stale-import failures as if they "
        "were broken changes.")


def test_the_guard_refuses_a_package_imported_from_a_foreign_root() -> None:
    """Naming another copy of the package exits before any test runs.

    This is the failure the guard exists for: an ``AttributeError`` for a helper
    a checkout test names but a pinned release predates reads as a broken change
    while describing a different tree.
    """
    foreign = _CHECKOUT_ROOT / ".mishe-tauftauf" / "releases" / \
        "a437ee8efa183cbecbc6f7b0e33ce008d098ec7b" / "src"
    completed = _probe(str(foreign), _CONFTEST, cwd="/tmp")
    assert completed.returncode != 0
    assert "not this checkout" in completed.stderr


def test_the_guard_refuses_silence_when_the_package_resolves_from_nowhere() -> None:
    """A package present here but imported from nowhere is a failure, not a pass.

    Silence would let the run proceed to collection errors that are not about
    the tree under test, which is the same misleading outcome the foreign root
    produces one step later.
    """
    scratch = Path("/tmp/guard-silence-probe")
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        (scratch / "src" / "mishe_tauftauf").mkdir(parents=True, exist_ok=True)
        (scratch / "src" / "mishe_tauftauf" / "__init__.py").write_text(
            "", encoding="utf-8")
        (scratch / "conftest.py").write_text(_CONFTEST.read_text(encoding="utf-8"),
                                             encoding="utf-8")
        completed = _probe("/nonexistent-root-for-the-guard-probe",
                       scratch / "conftest.py", cwd="/tmp", isolated=True)
        assert completed.returncode != 0
        assert "not importable" in completed.stderr
    finally:
        subprocess.run(["rm", "-rf", str(scratch)], check=False, timeout=60)


def test_the_guard_allows_the_checkout_package() -> None:
    """The intended command still runs: HEAD tests against HEAD."""
    completed = _probe(str(_CHECKOUT_ROOT / "src"), _CONFTEST, cwd="/tmp")
    assert completed.returncode == 0, completed.stderr
