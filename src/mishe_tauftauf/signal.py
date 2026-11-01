"""Pure reading algebra: compute a reading once, share it, let it expire.

A Signal is (value, observed_at, source, state). State is one of:

- OK: the value was observed fresh enough.
- STALE: the value was observed, but is older than its allowed age.
- UNKNOWN: no usable observation (missing, failed or unparseable input).

Combinators do no I/O and read no clock. Callers pass ``now`` explicitly,
so every rule is deterministic and replayable from recorded tapes.

Invariant: missing, failed or stale input is never reported as OK, except
``hold`` within max_age of the last OK reading (a deliberate read-gap
bridge; the held result keeps the previous observed_at).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

OK = "OK"
STALE = "STALE"
UNKNOWN = "UNKNOWN"

# Worst-state ranking used by join: UNKNOWN > STALE > OK.
_RANK = {OK: 0, STALE: 1, UNKNOWN: 2}


@dataclass(frozen=True)
class Signal:
    value: Any
    observed_at: float | None
    source: str
    state: str = OK

    def __post_init__(self) -> None:
        if self.state not in _RANK:
            raise ValueError(f"unknown signal state: {self.state!r}")
        if self.state == OK and self.observed_at is None:
            raise ValueError("an OK signal needs an observation time")


def observed(value: Any, observed_at: float, source: str) -> Signal:
    """A fresh observation. Expiry is applied later by ``expire``."""
    return Signal(value, observed_at, source, OK)


def unknown(source: str, reason: str = "", observed_at: float | None = None) -> Signal:
    """No usable observation. The reason is kept in the value for display."""
    return Signal(reason, observed_at, source, UNKNOWN)


def expire(signal: Signal, now: float, max_age: float) -> Signal:
    """Mark an OK signal STALE once its observation is older than max_age.

    STALE and UNKNOWN pass through unchanged. An OK signal observed in the
    future (clock skew) is not trusted and becomes STALE.
    """
    if signal.state != OK:
        return signal
    age = now - signal.observed_at
    if age < 0 or age > max_age:
        return replace(signal, state=STALE)
    return signal


def map_value(signal: Signal, fn: Callable[[Any], Any]) -> Signal:
    """Transform the value, keeping time, source and state.

    If ``fn`` raises, the result is UNKNOWN rather than an exception or a
    guessed OK value.
    """
    if signal.state == UNKNOWN:
        return signal
    try:
        value = fn(signal.value)
    except Exception as error:  # the transform is the boundary; report, do not guess
        return Signal(f"map failed: {error}", signal.observed_at, signal.source, UNKNOWN)
    return replace(signal, value=value)


def join(signals: Iterable[Signal], combine: Callable[[list], Any] = list,
         source: str = "join") -> Signal:
    """Combine several signals. The worst state wins.

    Values are passed to ``combine`` as a list in input order. The result's
    observed_at is the oldest contributing observation, so a join is never
    fresher than its least fresh input. With no inputs the result is UNKNOWN.
    """
    items = list(signals)
    if not items:
        return unknown(source, "no inputs")
    worst = max((s.state for s in items), key=_RANK.__getitem__)
    times = [s.observed_at for s in items if s.observed_at is not None]
    oldest = min(times) if len(times) == len(items) else None
    if worst == UNKNOWN:
        # Keep the reasons from the inputs that could not be read.
        reasons = [str(s.value) for s in items if s.state == UNKNOWN]
        return Signal("; ".join(reasons), oldest, source, UNKNOWN)
    return Signal(combine([s.value for s in items]), oldest, source, worst)


def hold(previous: Signal | None, current: Signal, now: float, max_age: float) -> Signal:
    """Keep the last OK reading for up to max_age, then expire it.

    ``current`` wins whenever it is OK or STALE. If ``current`` is UNKNOWN
    and ``previous`` is still fresh, the previous value is returned as OK so
    a transient read gap does not flip a consumer. Once previous expires, the
    current UNKNOWN is returned unchanged.
    """
    if current.state != UNKNOWN:
        return current
    if previous is None:
        return current
    held = expire(previous, now, max_age)
    return held if held.state == OK else current


def edge(previous_value: Any, current_value: Any) -> bool:
    """True when the value changed.

    ``None`` is the "no previous reading" sentinel, so the first reading is
    always an edge, even when that reading is itself None. Callers that need a
    real None value must track the first reading separately.
    """
    return previous_value is None or previous_value != current_value


def rate_limit(last_emit: float | None, now: float, min_interval: float) -> bool:
    """True when enough time has passed to emit again.

    A clock that stepped back (now < last_emit) is not trusted to measure the
    interval. It returns True, so emission resumes instead of being suppressed
    for the length of the step. The caller then records ``now`` as the new
    last_emit, which restores normal spacing.
    """
    if last_emit is None or now < last_emit:
        return True
    return now - last_emit >= min_interval
