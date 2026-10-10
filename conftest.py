"""Refuse to run this checkout's tests against a different copy of the package.

The seed launcher exports PYTHONPATH=<pinned release>/src into every role pane, so
a bare `pytest` here imports the pinned release, not HEAD. Stale source against
current tests then fails (or worse, passes) for reasons that have nothing to do
with the change under review, and the cause is invisible in the output. Fail
loudly instead, naming the imported root and the fix.

This file must ship in the release for the guard to protect anyone but its
author: an untracked conftest.py is absent from every release worktree, so a
checkout test run there falls through to the pinned package and reports
AttributeError for helpers the release predates instead of naming the cause.
"""

import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent / "src"
_PACKAGE = _ROOT / "mishe_tauftauf" / "__init__.py"


def _fail(message: str) -> None:
    raise SystemExit(message)


def _origin() -> pathlib.Path:
    if not _PACKAGE.is_file():
        _fail(
            "this checkout has no src/mishe_tauftauf/__init__.py (%s), so a test "
            "run here cannot resolve the package from the tree it is looking at."
            % _PACKAGE)
    try:
        spec = importlib.util.find_spec("mishe_tauftauf")
    except (ImportError, ValueError):
        spec = None
    if spec is None or spec.origin is None:
        # The package is present here but imported from nowhere: an inherited
        # search path shadows or breaks it, so collection errors would not be
        # about the tree under test.
        _fail(
            "mishe_tauftauf is not importable though %s is present; an inherited "
            "PYTHONPATH shadows or breaks the import.\n"
            "Test HEAD: env PYTHONPATH=src .venv/bin/pytest -q" % _PACKAGE)
    return pathlib.Path(spec.origin).resolve()


_origin_path = _origin()
if _origin_path != _PACKAGE.resolve():
    _fail(
        "mishe_tauftauf is imported from %s, not this checkout (%s).\n"
        "A bare `pytest` here tests the pinned release, not HEAD, so its result "
        "does not describe the tree you are looking at.\n"
        "Test HEAD: env PYTHONPATH=src .venv/bin/pytest -q\n"
        "Test a release deliberately: run that release's own tests, "
        "e.g. .venv/bin/pytest SITE_HOME/releases/<sha>/tests -q"
        % (_origin_path.parent.parent, _ROOT))


def pytest_configure(config):
    """Reap sessions a hard-killed earlier run leaked, before this one adds more.

    A hard kill skips every test's finally cleanup, so its tmux session and the
    detached mind under its pane survive with no owner. The sweep kills only
    sessions whose embedded creating pid is dead, so a concurrently running
    suite's live sessions are untouched.
    """
    from mishe_tauftauf.tmux import sweep_orphan_test_sessions

    sweep_orphan_test_sessions()
