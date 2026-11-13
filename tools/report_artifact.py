#!/usr/bin/env python3
"""Write a check's report without destroying evidence another outcome published.

A check's report is a *report*, not evidence. It is written to a stable path
under ``SITE_HOME/artifacts/`` so a reader can find the last reading, and the
plant's outcome clause can bind that path as write-once evidence: the outcome
records the file's digest and the writer makes the file read-only at the
moment it binds it (``0444``).

That leaves the report path unable to be rewritten, and a check that writes
its report with a plain ``write_text`` now crashes with ``PermissionError``.
The crash is worse than a stale report, because a check's exit status is how
a reader learns whether the book is sound: a tool that dies while writing its
report cannot say "the corpus is clean" and cannot say "the corpus names a
command that does not exist" either.

So a report is written under two rules:

* **The bound bytes are never touched.** When the stable path cannot be
  written, the report goes to a dated sibling next to it —
  ``<stem>-<YYYYMMDDTHHMMSSZ><suffix>`` — so the reading is still published
  and the published evidence keeps its digest.
* **A report that cannot be written never decides the verdict.** The caller
  reads the returned status and keeps its own exit code. A missing report is
  a missing report, not a failing audit.

``write_report`` returns ``(path, status)``. ``status`` is ``"written"`` when
the stable path took the bytes, ``"sibling"`` when a dated sibling took them
and ``"unwritten"`` when nothing could be written; the last case carries the
reason in ``status_detail``.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

#: The suffix a dated sibling carries between the stem and the extension.
DATE_FORMAT = "%Y%m%dT%H%M%SZ"


def sibling_path(path: Path, now: datetime | None = None) -> Path:
    """The dated sibling path for ``path``: same directory, same suffix."""
    stamp = (now or datetime.now(timezone.utc)).strftime(DATE_FORMAT)
    return path.with_name(f"{path.stem}-{stamp}{path.suffix}")


def _write(path: Path, text: str) -> bool:
    """Write ``text`` to ``path``; True when the bytes are there."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def write_report(path: Path, text: str, *, now: datetime | None = None) -> tuple[Path, str, str]:
    """Publish a report, preferring the stable path and never breaking the audit.

    Returns ``(path, status, detail)``. ``status`` is ``"written"``,
    ``"sibling"`` or ``"unwritten"``; ``detail`` carries the reason for the
    last two. The stable path is left untouched when it cannot be written, so
    bytes an outcome published keep their digest.
    """
    path = Path(path)
    if _write(path, text):
        return path, "written", ""
    sibling = sibling_path(path, now)
    if _write(sibling, text):
        return sibling, "sibling", (f"stable path {path} is not writable; "
                                    f"the report was written to {sibling}")
    return path, "unwritten", (f"neither {path} nor {sibling} could be written")


def read_only(path: Path) -> bool:
    """True when any write bit is set on ``path`` — the condition that lets a
    later edit drop it."""
    try:
        return bool(os.stat(path).st_mode & 0o222)
    except OSError:
        return False
