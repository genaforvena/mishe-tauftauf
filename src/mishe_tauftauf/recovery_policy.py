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
