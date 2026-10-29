"""Recovery decisions over decoded observations, never over outcome prose."""
from __future__ import annotations

import math
import statistics


def transition(previous, *, name, result, now, owner, backup, deadline,
               stable_seconds, mitigation=False, max_gap=90):
    if result is not None and type(result) is not bool:
        raise ValueError("probe result must be true, false or unknown")
    if not all(math.isfinite(n) and n >= 0 for n in (now, deadline, stable_seconds)):
        raise ValueError("invalid recovery time")
    row = dict(previous or {
        "name": name, "episode": 1, "first_failed": None,
        "passing_since": None, "restored_at": None,
        "previous_restored_at": None, "resolver": owner,
        "backup_requested": None, "backup_accepted_at": None,
    })
    if previous and now < previous["checked_at"]:
        result = None
    if previous and now - previous["checked_at"] > max_gap:
        row["passing_since"] = None
    if result is not True and row["restored_at"] is not None:
        row.update(episode=row["episode"] + 1,
                   previous_restored_at=row["restored_at"], restored_at=None,
                   first_failed=None, resolver=owner, backup_requested=None,
                   backup_accepted_at=None)
    row["checked_at"] = now
    if result is not True:
        if row["first_failed"] is None:
            row["first_failed"] = now
        row["passing_since"] = None
        row["state"] = "UNKNOWN" if result is None else "MITIGATED" if mitigation else "FAILED"
    elif row["first_failed"] is None:
        row["state"] = "HEALTHY"
    else:
        if row["passing_since"] is None:
            row["passing_since"] = now
        if now - row["passing_since"] >= stable_seconds:
            row["state"] = "RESTORED"
            if row["restored_at"] is None:
                row["restored_at"] = now
        else:
            row["state"] = "RECOVERING"
    if (row["first_failed"] is not None and row["restored_at"] is None
            and now - row["first_failed"] >= deadline):
        row["backup_requested"] = backup
    return row


def accept(row, *, owner, now):
    if owner != row["backup_requested"] or row["restored_at"] is not None:
        raise ValueError("no matching open backup request")
    return dict(row, resolver=owner, backup_accepted_at=now)


def capacity(previous, *, memory_full, io_full, available_ratio, now,
             recover_seconds, memory_high=10, io_high=40, available_low=.08,
             max_gap=90):
    values = (memory_full, io_full, available_ratio)
    if (any(v is None or not math.isfinite(v) or v < 0 for v in values)
            or memory_full > 100 or io_full > 100 or available_ratio > 1):
        return {"state": "UNKNOWN", "limited": True, "clear_since": None,
                "checked_at": now}
    overloaded = memory_full >= memory_high or (io_full >= io_high and available_ratio < available_low)
    clear = memory_full < memory_high / 2 and io_full < io_high / 2 and available_ratio >= available_low * 1.5
    limited = overloaded or (previous or {}).get("limited", False)
    clear_since = (previous or {}).get("clear_since") if clear else None
    if previous and not 0 <= now - previous["checked_at"] <= max_gap:
        clear_since = None
    if clear and clear_since is None:
        clear_since = now
    if clear_since is not None and now - clear_since >= recover_seconds:
        limited = False
    return {"state": "DEGRADED" if limited else "NORMAL", "limited": limited,
            "clear_since": clear_since, "checked_at": now}


def compare(before, after):
    def summary(rows):
        known = [r for r in rows if type(r.get("control_ok")) is bool]
        return {"samples": len(rows), "known_control_samples": len(known),
                "control_success_rate": sum(r["control_ok"] for r in known) / len(known) if known else None,
                **{key: statistics.median([r[key] for r in rows if r.get(key) is not None])
                   if any(r.get(key) is not None for r in rows) else None
                   for key in ("memory_full", "io_full")}}
    a, b = summary(before), summary(after)
    available = bool(a["known_control_samples"] and b["known_control_samples"])
    improved = available and b["control_success_rate"] > a["control_success_rate"]
    return {"before": a, "after": b, "functional_improvement": bool(improved),
            "verdict": "IMPROVED" if improved else "NO_FUNCTIONAL_IMPROVEMENT" if available else "UNKNOWN",
            "causation": "uncontrolled before/after; attribution unproven"}


def breaker_transition(previous, *, result, now, threshold=5, window=60,
                       min_calls=5, failure_rate_threshold=0.5,
                       reset_timeout=120, is_transient=None):
    """Circuit breaker beside transition/capacity — closed/open/half_open over a rolling window.

    Pure function, no I/O. State carried in a plain dict mirroring transition's row.
    Keys: state ∈ {CLOSED, OPEN, HALF_OPEN}, failure_count, call_count,
          window_start, opened_at, last_failure_time, checked_at.
    Initial row: CLOSED, failure_count=0.

    Parameters
    ----------
    result: True = probe passed / attempt settled, False = failed, None = UNKNOWN.
    is_transient: whether a False failure is trippable (transient/timeout/OSError).
                  None defaults to True for False (so transient 500 trips); pass
                  False for deterministic rejections (e.g. 422) that must not trip.
                  Only a trippable False increments failure_count within window.
    threshold, window, min_calls, failure_rate_threshold, reset_timeout:
              trip when calls_in_window >= min_calls and failure_count >= threshold
              and failure_count / calls_in_window >= failure_rate_threshold.
              While OPEN and now - opened_at <= reset_timeout the circuit stays OPEN
              and no read of supplier state should occur. After the timeout the next
              call moves to HALF_OPEN and admits one trial; a True trial resets to
              CLOSED, a trippable False re-opens.
    """
    if result is not None and type(result) is not bool:
        raise ValueError("breaker result must be true, false or unknown")
    if not math.isfinite(now) or now < 0:
        raise ValueError("invalid breaker time")
    if not (isinstance(threshold, int) and threshold >= 1):
        raise ValueError("threshold must be positive int")
    if not (math.isfinite(window) and window > 0):
        raise ValueError("window must be positive")
    if not (isinstance(min_calls, int) and min_calls >= 1):
        raise ValueError("min_calls must be positive int")
    if not (math.isfinite(failure_rate_threshold) and 0 < failure_rate_threshold <= 1):
        raise ValueError("failure_rate_threshold must be in (0,1]")
    if not (math.isfinite(reset_timeout) and reset_timeout > 0):
        raise ValueError("reset_timeout must be positive")

    trippable = False
    if result is False:
        if is_transient is None:
            trippable = True
        else:
            trippable = bool(is_transient)

    row = dict(previous) if isinstance(previous, dict) and previous else {}
    # defaults for a fresh circuit
    if not row:
        row = {
            "state": "CLOSED",
            "failure_count": 0,
            "call_count": 0,
            "window_start": None,
            "opened_at": None,
            "last_failure_time": None,
            "checked_at": now,
        }
    else:
        # ensure expected keys exist
        row.setdefault("state", "CLOSED")
        row.setdefault("failure_count", 0)
        row.setdefault("call_count", 0)
        row.setdefault("window_start", None)
        row.setdefault("opened_at", None)
        row.setdefault("last_failure_time", None)
    # normalise state value
    if row["state"] not in {"CLOSED", "OPEN", "HALF_OPEN"}:
        row["state"] = "CLOSED"

    # Guard against clock going backwards — treat as UNKNOWN-like no-op for breaker
    if row.get("checked_at") is not None and now < row["checked_at"]:
        row = dict(row)
        row["checked_at"] = now
        return row

    prev_state = row["state"]

    # OPEN: stay open until reset_timeout elapses
    if prev_state == "OPEN":
        opened_at = row.get("opened_at")
        if opened_at is not None and now - opened_at <= reset_timeout:
            row = dict(row)
            row["checked_at"] = now
            return row
        # timeout elapsed -> move to HALF_OPEN and admit one trial
        row = dict(row)
        row["state"] = "HALF_OPEN"
        row["checked_at"] = now
        # The trial's result decides the next state on this same call:
        if result is True:
            # success resets
            row["state"] = "CLOSED"
            row["failure_count"] = 0
            row["call_count"] = 0
            row["window_start"] = now
            row["opened_at"] = None
            row["last_failure_time"] = None
        elif result is False and trippable:
            row["state"] = "OPEN"
            row["opened_at"] = now
            row["last_failure_time"] = now
            # keep window counts; half-open failure restarts window
            row["failure_count"] = int(row.get("failure_count", 0)) + 0  # unchanged except re-open
        elif result is False and not trippable:
            # non-trippable failure in HALF_OPEN does not re-open; stay HALF_OPEN
            # let caller decide; remain HALF_OPEN
            pass
        # result None stays HALF_OPEN
        return row

    if prev_state == "HALF_OPEN":
        row = dict(row)
        row["checked_at"] = now
        if result is True:
            row["state"] = "CLOSED"
            row["failure_count"] = 0
            row["call_count"] = 0
            row["window_start"] = now
            row["opened_at"] = None
            row["last_failure_time"] = None
        elif result is False and trippable:
            row["state"] = "OPEN"
            row["opened_at"] = now
            row["last_failure_time"] = now
        # None or non-trippable stays HALF_OPEN
        return row

    # CLOSED
    row = dict(row)
    # window rotation
    window_start = row.get("window_start")
    if window_start is None or now - window_start > window:
        row["window_start"] = now
        row["call_count"] = 0
        row["failure_count"] = 0
    # UNKNOWN does not count as a call for rate
    if result is None:
        row["checked_at"] = now
        return row
    # Count this call
    row["call_count"] = int(row.get("call_count", 0)) + 1
    row["checked_at"] = now
    if result is False and trippable:
        row["failure_count"] = int(row.get("failure_count", 0)) + 1
        row["last_failure_time"] = now
    elif result is False and not trippable:
        # deterministic failure: counted as call but not as tripping failure
        pass
    else:
        # success: failure_count unchanged, but call_count already incremented
        pass
    # trip check
    calls = int(row.get("call_count", 0))
    fails = int(row.get("failure_count", 0))
    if calls >= min_calls and fails >= threshold and calls > 0 and (fails / calls) >= failure_rate_threshold:
        row["state"] = "OPEN"
        row["opened_at"] = now
    else:
        row["state"] = "CLOSED"
    return row
