"""Consumer-side reader for the freshness verdict (``mishe-signal-freshness``).

The verdict is ``now - seen > max_age``, the comparison ``signal_store.read`` makes.
The binary is ``rust/mishe-signal-freshness`` (contract: its CONTRACT.md). The verdict
is the exit status, so this reader parses no output: exit 0 is FRESH, exit 1 is
EXPIRED, and anything else (2, a signal, a timeout, a missing binary) is UNKNOWN.
There is no Python fallback, so a broken binary can never make a stale signal look
fresh.

Not yet on a live path. ``signal_store.read`` still compares in Python.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV_OVERRIDE = "MISHE_SIGNAL_FRESHNESS_BIN"
FRESH = "FRESH"
EXPIRED = "EXPIRED"


class FreshnessUnknown(RuntimeError):
    """The verdict cannot be read. Callers must treat the signal as UNKNOWN."""


def resolve_binary(explicit: str | None = None) -> Path:
    """Explicit argument, then the environment override. Fails closed."""
    chosen = explicit or os.environ.get(ENV_OVERRIDE)
    if not chosen:
        raise FreshnessUnknown(f"no mishe-signal-freshness binary: pass one or set {ENV_OVERRIDE}")
    path = Path(chosen)
    if not (path.is_file() and os.access(path, os.X_OK)):
        raise FreshnessUnknown(f"mishe-signal-freshness is not an executable file: {path}")
    return path


def verdict(seen: float, now: float, max_age: float, *, binary: str | None = None,
            timeout: float = 10.0) -> str:
    """FRESH or EXPIRED for one observation, or FreshnessUnknown."""
    exe = resolve_binary(binary)
    args = [str(exe)] + [repr(float(value)) for value in (seen, now, max_age)]
    try:
        done = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FreshnessUnknown(f"mishe-signal-freshness did not run: {exc}") from exc
    if done.returncode == 0:
        return FRESH
    if done.returncode == 1:
        return EXPIRED
    raise FreshnessUnknown(f"mishe-signal-freshness exited {done.returncode}")


def verdicts(triples, *, binary: str | None = None) -> list[str]:
    """One verdict per ``(seen, now, max_age)`` triple, in order; all or nothing."""
    return [verdict(seen, now, max_age, binary=binary) for seen, now, max_age in triples]
