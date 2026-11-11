"""Resolve the mishe-records binary for tests, hermetically.

Order: ``MISHE_RECORDS_BIN`` (explicit override), then ``$MISHE_SEED_HOME/bin/mishe-records``
(then ``$MISHE_TAUFTAUF_HOME``, as the product reader does). The site install must match
the sha256 in ``mishe-records.install.json`` next to it — the contract's release rule, the
crate is not offline-buildable. If nothing resolves, a test is skipped with the reason, so
a checkout without a built binary runs the rest of the suite and says exactly what is
missing.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest


def resolve_records_binary() -> tuple[str | None, str]:
    """Return ``(path, "")`` when a usable binary exists, else ``(None, reason)``."""
    override = os.environ.get("MISHE_RECORDS_BIN")
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override, ""
        return None, "MISHE_RECORDS_BIN names no executable file"
    home = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    if not home:
        return None, "mishe-records not available: set MISHE_RECORDS_BIN or MISHE_SEED_HOME"
    candidate = Path(home) / "bin" / "mishe-records"
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None, f"mishe-records not installed at {candidate}"
    try:
        recorded = json.loads(candidate.with_name("mishe-records.install.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc:
        return None, f"mishe-records install unreadable: {exc}"
    if recorded.get("sha256") != digest:
        return None, "mishe-records install does not match its recorded sha256"
    return str(candidate), ""


RECORDS_BIN, RECORDS_SKIP_REASON = resolve_records_binary()


def requires_records_binary(test):
    """Skip the test when no verifier resolves; otherwise run it with MISHE_RECORDS_BIN pinned."""
    if RECORDS_BIN is None:
        return unittest.skip(RECORDS_SKIP_REASON)(test)

    @functools.wraps(test)
    def run(self, *args, **kwargs):
        with patch.dict(os.environ, {"MISHE_RECORDS_BIN": RECORDS_BIN}):
            return test(self, *args, **kwargs)

    return run


@pytest.fixture
def pin_records_binary(monkeypatch):
    """Pin ``MISHE_RECORDS_BIN`` for a test that drives record verification.

    The feed reader resolves the verifier through the product rule (an explicit
    override, then the site install), and the suite runs hermetically without a
    site binding, so a test that verifies a record must name the binary itself.
    Skips with the reason when no binary resolves.
    """
    if RECORDS_BIN is None:
        pytest.skip(RECORDS_SKIP_REASON)
    monkeypatch.setenv("MISHE_RECORDS_BIN", RECORDS_BIN)
