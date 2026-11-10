"""Resolve the mishe-records binary for tests, hermetically.

Order: ``MISHE_RECORDS_BIN`` (explicit override), then ``$MISHE_SEED_HOME/bin/mishe-records``
(then ``$MISHE_TAUFTAUF_HOME``, as the product reader does). The site install must match
the sha256 in ``mishe-records.install.json`` next to it — the contract's release rule, the
crate is not offline-buildable. If nothing resolves, a test is skipped with the reason, so
a checkout without a built binary runs the rest of the suite and says exactly what is
missing.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


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
