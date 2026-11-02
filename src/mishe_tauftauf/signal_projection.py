"""Live dashboard projection: publish a frame when the reading changes, not on a timer.

The charter's contract for the slice:

    a "live dashboard" projection that writes the same text/JSON frame format as
    today (`dashboards/ROLE.json`: created, sha256, ok) on change with a minimum
    interval

This module is the missing composition of the pure algebra. ``live_signal`` already
provides the parts — ``changed`` (an edge on a fresh value) and ``rate_limit`` (a
minimum interval between emissions) — but nothing joined them to the plant's frame
contract, so a reader had to re-derive every frame from scratch on every tick.

Pure and I/O free, like the rest of the slice: the caller supplies the reading and
the clock; the caller performs the write through ``mishe_tauftauf.dashboard``.

Two rules matter and are enforced here rather than left to the caller:

- A reading that is not OK never produces a frame. A missing, failed or stale
  reading is UNKNOWN or STALE, never OK, and the frame says so instead of showing
  a value it does not have.
- An unchanged reading is not re-emitted before the minimum interval has passed.
  Between the interval, the previous frame stands. This is the difference the
  charter asks for: the frame is published when the reading changes, and at most
  once per interval otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import live_signal as ls

__all__ = ["Projection", "Decision", "decide_publish", "UNKNOWN_FRAME"]


UNKNOWN_FRAME = "UNKNOWN — reading unavailable; no fresh frame published"


@dataclass(frozen=True)
class Decision:
    """What the projection would do with one reading."""

    publish: bool
    reason: str
    frame: str | None = None
    signal: ls.Signal | None = None


@dataclass(frozen=True)
class Projection:
    """The projection's own memory: what was last published, and when."""

    last_value: object = None
    last_emitted_at: float | None = None
    last_frame: str | None = None
    published_once: bool = False


def decide_publish(reading: ls.Signal, projection: Projection, *, now: float,
                   render, min_interval: float = 30.0,
                   max_age: float = 60.0) -> tuple[Decision, Projection]:
    """Decide whether ``reading`` should produce a frame, and return the new projection.

    ``render`` maps a fresh value to the frame text. It is called only when a frame
    will actually be published, so an unchanged reading costs no rendering.

    The returned projection is the state to keep: it records the published value and
    time, and nothing else. A not-published decision leaves it unchanged, except
    that the first publication is remembered even if it renders later.
    """
    held = ls.hold_until_expiry(reading, now, max_age)
    if held.state != ls.OK:
        # A missing, failed or stale reading is never published as a value. The
        # caller may still publish UNKNOWN_FRAME if it wants the frame to say so.
        return Decision(False, f"reading is {held.state}: {held.reason or held.source}"), projection

    first = not projection.published_once
    edge = ls.changed(projection.last_value, held)
    due = ls.rate_limit(projection.last_emitted_at, now, min_interval)

    if not first and not edge and not due:
        return Decision(False, "unchanged and inside the minimum interval"), projection

    if not first and not edge and due:
        # Inside the interval is false, so this is the periodic refresh: same value,
        # but the frame must not go stale for readers.
        reason = "periodic refresh"
    elif first:
        reason = "first publication"
    else:
        reason = "reading changed"

    try:
        frame = render(held.value)
    except (ArithmeticError, LookupError, TypeError, ValueError) as exc:
        return Decision(False, f"render failed: {exc}"), projection
    if not isinstance(frame, str) or not frame:
        return Decision(False, "render produced no frame"), projection

    updated = Projection(last_value=held.value, last_emitted_at=now,
                          last_frame=frame, published_once=True)
    return Decision(True, reason, frame, held), updated
