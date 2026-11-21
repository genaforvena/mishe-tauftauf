"""Boundary adapter: the signal-coverage duty's declared event identity.

Only the declaring duty (``DECLARING_DUTY``) may declare an event. Its check prints a final
``signal-event: <digest>`` line; this adapter is the only place that reads that line, and
every other duty's diagnostic text stays plain reason text that never moves a revision.
"""
from __future__ import annotations

from pathlib import Path

DECLARING_DUTY = "signal-coverage"
EVENT_MARK = "signal-event: "   # keep in step with checks/signal-coverage.py EVENT_MARK


def declared_event(name: str, detail: Path) -> str | None:
    """The declaring duty's event digest from the final detail line, or None for any other duty."""
    if name != DECLARING_DUTY:
        return None
    try:
        lines = Path(detail).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    last = lines[-1].strip() if lines else ""
    if last.startswith(EVENT_MARK):
        return last[len(EVENT_MARK):].strip() or None
    return None


def without_event(reason: str, event: str) -> str:
    """The reason text with the declared event line removed, so the digest is not shown as cause text."""
    return reason.replace(f"{EVENT_MARK}{event}", "").strip()
