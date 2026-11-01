"""Pure reading algebra for readings of the external world.

A Signal is (value, observed_at, source, state). State is OK, STALE or UNKNOWN.
Missing, failed or expired input is never reported as OK. Combinators take an
explicit `now`; nothing here reads a clock or performs I/O.

Named live_signal (not signal) so it never shadows the standard library module.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Iterable

OK = "OK"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
_RANK = {OK: 0, STALE: 1, UNKNOWN: 2}


@dataclass(frozen=True)
class Signal:
    value: Any
    observed_at: float | None
    source: str
    state: str = OK
    reason: str = ""

    def __post_init__(self):
        if self.state not in _RANK:
            raise ValueError(f"unknown signal state {self.state!r}")
        if self.state != OK and self.value is not None:
            # Only a fresh OK reading may carry a value that consumers act on.
            object.__setattr__(self, "value", None)


def observe(value: Any, *, at: float, source: str) -> Signal:
    """A fresh reading. Non-finite numbers are a failed observation."""
    if value is None:
        return fail(source, at=at, reason="no value")
    if isinstance(value, float) and not math.isfinite(value):
        return fail(source, at=at, reason="non-finite value")
    return Signal(value, at, source, OK)


def fail(source: str, *, at: float | None, reason: str) -> Signal:
    return Signal(None, at, source, UNKNOWN, reason)


def age(sig: Signal, now: float) -> float | None:
    return None if sig.observed_at is None else now - sig.observed_at


def hold_until_expiry(sig: Signal, now: float, max_age: float) -> Signal:
    """Keep an OK reading valid for max_age seconds, then mark it STALE.

    A reading from the future (clock stepped back or bad input) is UNKNOWN.
    Non-OK input passes through unchanged.
    """
    if sig.state != OK:
        return sig
    elapsed = age(sig, now)
    if elapsed is None or elapsed < 0:
        return fail(sig.source, at=sig.observed_at, reason="observation after now")
    if elapsed > max_age:
        return Signal(None, sig.observed_at, sig.source, STALE,
                      f"expired after {max_age:g}s (age {elapsed:.1f}s)")
    return sig


def map_(sig: Signal, fn: Callable[[Any], Any]) -> Signal:
    """Transform a fresh value. Non-OK states propagate; an exception is UNKNOWN."""
    if sig.state != OK:
        return sig
    try:
        result = fn(sig.value)
    except (ArithmeticError, LookupError, TypeError, ValueError) as exc:
        return fail(sig.source, at=sig.observed_at, reason=f"map failed: {exc}")
    return observe(result, at=sig.observed_at, source=sig.source)


def join(sigs: Iterable[Signal], fn: Callable[..., Any], *, source: str) -> Signal:
    """Combine readings. Worst state wins (UNKNOWN > STALE > OK).

    The combined observed_at is the oldest input, so a join is only as fresh
    as its stalest part. Value is fn(*values) only when every input is OK.
    """
    items = list(sigs)
    if not items:
        return fail(source, at=None, reason="no inputs")
    worst = max(items, key=lambda s: _RANK[s.state])
    if any(s.observed_at is None for s in items):
        return fail(source, at=None, reason="input without observation time")
    at = min(s.observed_at for s in items)
    if worst.state != OK:
        return Signal(None, at, source, worst.state, worst.reason or worst.source)
    try:
        value = fn(*[s.value for s in items])
    except (ArithmeticError, LookupError, TypeError, ValueError) as exc:
        return fail(source, at=at, reason=f"join failed: {exc}")
    return observe(value, at=at, source=source)


def changed(previous: Any, sig: Signal) -> bool:
    """True when a fresh reading differs from the previous fresh value (edge)."""
    return sig.state == OK and sig.value != previous


def rate_limit(last_emit: float | None, now: float, min_interval: float) -> bool:
    """True when an emission is allowed: first emission, or min_interval elapsed."""
    if last_emit is None:
        return True
    return now - last_emit >= min_interval
