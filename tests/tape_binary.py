"""Resolve the mishe-tape binary for tests, hermetically.

Order: ``MISHE_TAPE_BIN`` (explicit override), then ``$MISHE_SEED_HOME/bin/mishe-tape``
(then ``$MISHE_TAUFTAUF_HOME``, as the product reader does). If none resolves, a
test is skipped with the reason, so a checkout without a built framer runs the
rest of the suite and says exactly what is missing. No test depends on a
toolchain or on CI-only build steps.
"""
from __future__ import annotations

import functools
import os
import unittest
from pathlib import Path
from unittest.mock import patch


def resolve_tape_binary() -> tuple[str | None, str]:
    """Return ``(path, "")`` when a usable binary exists, else ``(None, reason)``."""
    override = os.environ.get("MISHE_TAPE_BIN")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override, ""
        return None, "MISHE_TAPE_BIN names no executable file"
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        return None, "mishe-tape not available: set MISHE_TAPE_BIN or MISHE_SEED_HOME"
    candidate = Path(home) / "bin" / "mishe-tape"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate), ""
    return None, f"mishe-tape not installed at {candidate}"


TAPE_BIN, TAPE_SKIP_REASON = resolve_tape_binary()


def requires_tape_binary(test):
    """Skip the test when no framer resolves; otherwise run it with MISHE_TAPE_BIN pinned."""
    if TAPE_BIN is None:
        return unittest.skip(TAPE_SKIP_REASON)(test)

    @functools.wraps(test)
    def run(self, *args, **kwargs):
        with patch.dict(os.environ, {"MISHE_TAPE_BIN": TAPE_BIN}):
            return test(self, *args, **kwargs)

    return run
