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
    if not isinstance(rows, list) or any(
            not isinstance(r, dict) or (r.get("error") and r.get("account", account) == account) for r in rows):
        return "UNKNOWN", "budget collection or accounting incomplete"
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
    if any(r["verdict"] != "ok" for r in selected):
        return "RED", "provider's existing quota or burn rule holds spontaneous work"
    return "GREEN", "both provider windows satisfy the existing budget policy"


FRESH_S = 900


def rows_from_engine_budget(home, now):
    """Rows for ``evaluate`` from the producer's engine/budget.json (the one budget source since the 2026-11-12 cutover).

    The verdict keeps the pre-cutover rule: LOW on a spent window (used > 0.9), RUNS OUT BEFORE RESET when the burn ETA
    lands before the reset, otherwise ok. An unreadable file, a malformed account or a stale observation becomes an
    error row or a non-FRESH row, so ``evaluate`` reports UNKNOWN or STALE and never admits spontaneous work.
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
            rows.append(dict(account=account, error=f"{account}: account state {body.get('state') if isinstance(body, dict) else 'malformed'}"))
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
        signal_store.observe(Path(home), name, state, now=now, source=source)
    return dict(state=state, reason=reason)
