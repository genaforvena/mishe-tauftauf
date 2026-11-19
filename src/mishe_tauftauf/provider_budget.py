"""Scope existing quota verdicts to the provider used by a mind.

This does not collect accounts, change limits or invent a burn forecast. It
consumes decoded rows from the site's existing budget view and preserves its
freshness/forecast verdicts. Missing evidence never admits spontaneous work.
"""
import math
from pathlib import Path

from . import signal_store, state_files


def evaluate(rows, account, *, now):
    if account != "claude":
        return "UNKNOWN", "provider window contract not configured"
    # An error tagged with another account (e.g. codex-a STALE) does not make this provider's evidence incomplete;
    # an untagged error (unreadable file, accounting failure) still does.
    if not isinstance(rows, list):
        return "UNKNOWN", "budget collection or accounting incomplete: evidence is not a row list"
    for r in rows:
        if not isinstance(r, dict):
            return "UNKNOWN", "budget collection or accounting incomplete: malformed evidence row"
        if r.get("error") and r.get("account", account) == account:
            # Carry the cause, not a fixed phrase: a subscriber woken by UNKNOWN must see which read
            # failed (2026-11-17: the live claude UNKNOWN said "collection or accounting incomplete"
            # while the missing piece was its idle five_hour window).
            return "UNKNOWN", f"budget collection or accounting incomplete: {r['error']}"
    selected = [r for r in rows if r.get("account") == account]
    if (len(selected) != 2 or any(r.get("window") not in ("5h", "7d") for r in selected)
            or {r["window"] for r in selected} != {"5h", "7d"}):
        return "UNKNOWN", "provider windows missing or duplicated"
    for row in selected:
        used, reset = row.get("used"), row.get("reset")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (used, reset)):
            return "UNKNOWN", "provider quota is malformed"
        try:
            valid = math.isfinite(used) and 0 <= used <= 1 and math.isfinite(reset) and reset > now
        except OverflowError:
            valid = False
        if not valid or row.get("state") != "FRESH":
            return "UNKNOWN", "provider quota is missing, stale or malformed"
        if row.get("verdict") not in ("ok", "LOW", "RUNS OUT BEFORE RESET"):
            return "UNKNOWN", "provider forecast is unavailable"
    holding = [f"{r['window']} {r['verdict']} ({r['used']:.0%} used)"
               for r in selected if r["verdict"] != "ok"]
    if holding:
        # Name the window and rule: a subscriber woken by RED must see that a forward-looking
        # burn forecast on a lightly used window can hold work, not only a spent account
        # (2026-11-19: 5h 9%, 7d 24%, RED solely on "7d RUNS OUT BEFORE RESET").
        return "RED", ("provider's existing quota or burn rule holds spontaneous work: "
                       + ", ".join(holding))
    return "GREEN", "both provider windows satisfy the existing budget policy"


FRESH_S = 900

# A burn forecast from a near-empty window is a short-span extrapolation, not an
# actionable overburn (2026-11-18: 7d used 0.01 after ~31 min gave eta ~52 h against a
# reset ~161 h away, flipping budget-claude RED right after the weekly reset).
BURN_FORECAST_MIN_USED = 0.05


def rows_from_engine_budget(home, now):
    """Rows for ``evaluate`` from the producer's engine/budget.json (the one budget source since the 2026-11-12 cutover).

    The verdict keeps the pre-cutover rule: LOW on a spent window (used > 0.9), RUNS OUT BEFORE RESET when the burn ETA
    lands before the reset, otherwise ok. A forecast on a window below ``BURN_FORECAST_MIN_USED`` is a short-span
    extrapolation and stays ok. An unreadable file, a malformed account or a stale observation becomes an error row or
    a non-FRESH row, so ``evaluate`` reports UNKNOWN or STALE and never admits spontaneous work.
    """
    try:
        data = state_files.read_json(Path(home) / "engine" / "budget.json")
    except (OSError, ValueError) as error:
        return [dict(error=f"engine/budget.json unreadable: {str(error)[:120]}")]
    if data is None:
        return [dict(error="engine/budget.json missing")]
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict) or not accounts:
        return [dict(error="engine/budget.json has no accounts")]
    rows = []
    for account, body in accounts.items():
        if not isinstance(body, dict) or body.get("state") != "FRESH":
            if isinstance(body, dict):
                detail = f"{account}: account state {body.get('state')}"
                reason = body.get("reason")
                if isinstance(reason, str) and reason:
                    detail += f" ({reason})"
            else:
                detail = f"{account}: account state malformed"
            rows.append(dict(account=account, error=detail))
            continue
        windows = body.get("windows")
        if not isinstance(windows, dict) or not windows:
            rows.append(dict(account=account, error=f"{account}: FRESH account has no windows observed"))
            continue
        for window, x in windows.items():
            if not isinstance(x, dict):
                rows.append(dict(account=account, error=f"{account}/{window}: malformed window"))
                continue
            used, reset, observed = x.get("used"), x.get("reset_at"), x.get("observed_at")
            fresh = (isinstance(observed, (int, float)) and not isinstance(observed, bool)
                     and now - FRESH_S < observed <= now + 5 and x.get("state") == "FRESH")
            eta = x.get("eta_s")
            if not isinstance(used, (int, float)) or isinstance(used, bool) or not isinstance(reset, (int, float)):
                verdict = "UNKNOWN"
            elif not fresh:
                verdict = "STALE"
            elif used > 0.9:
                verdict = "LOW"
            elif used < BURN_FORECAST_MIN_USED:
                verdict = "ok"
            elif isinstance(eta, (int, float)) and not isinstance(eta, bool) and now + eta < reset:
                verdict = "RUNS OUT BEFORE RESET"
            else:
                verdict = "ok"
            rows.append(dict(account=account, window=window, used=used, reset=reset,
                             state="FRESH" if fresh else "STALE", verdict=verdict))
    return rows


def publish(home, rows, *, now):
    state, reason = evaluate(rows, "claude", now=now)
    name = "budget-claude"
    if name not in signal_store.registry(Path(home)):
        signal_store.register(Path(home), name, max_age=900, subscribers=[])
    source = "existing-budget-view:claude"
    if state == "UNKNOWN":
        signal_store.observe_unknown(Path(home), name, now=now, reason=reason, source=source)
    else:
        # Carry the cause into the reading: a RED with an empty reason tells a subscriber the
        # provider holds work but not which window or rule did it (2026-11-16: the live
        # budget-claude RED recorded reason="" while ``evaluate`` had already named the cause).
        signal_store.observe(Path(home), name, state, now=now, source=source, reason=reason)
    return dict(state=state, reason=reason)
