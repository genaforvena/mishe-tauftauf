"""Scope existing quota verdicts to the provider used by a mind.

It reports freshness and forecast verdicts from the site's existing budget
view. Wake admission also considers free routes.
"""
import math
from pathlib import Path

from . import signal_store, state_files


# Both accounts publish the producer's own 5h/7d verdicts. codex-a joined when
# gpt-6.2-sol became tier 0 in three roles (2026-11-20): its forecast was in
# engine/budget.json but no signal exposed it, so a burn-out-before-reset verdict
# was invisible beside the aggregate GREEN budget signal.
SIGNALS = {"claude": "budget-claude", "codex-a": "budget-codex"}


def evaluate(rows, account, *, now):
    if account not in SIGNALS:
        return "UNKNOWN", f"provider window contract not configured for {account}"
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
        return "RED", ", ".join(holding)
    return "GREEN", "both provider windows satisfy the existing budget policy"


FRESH_S = 900

# A burn forecast from a near-empty window is a short-span extrapolation, not an
# actionable overburn (2026-11-18: 7d used 0.01 after ~31 min gave eta ~52 h against a
# reset ~161 h away, flipping budget-claude RED right after the weekly reset).
BURN_FORECAST_MIN_USED = 0.05

# Windows whose owner has decided to run to the limit. Ilya, 2026-11-21: "let haiku run past 50%,
# weekly limit is fine". A burn projection that lands before the reset on such a window is
# information, not a verdict (it would otherwise hold work and re-wake the steward every hour for a
# limit that is accepted). A SPENT window (used > 0.9) still reads LOW, and the rolling 5 h window,
# which can stall work within hours, keeps its projection. Remove an entry to restore the alarm.
PROJECTION_ACCEPTED = {("claude", "7d")}

REOFFER_S = 3600
"""Re-offer cadence for the budget-claude signal.

``yielding.settle`` arms a known disposition's ``recheck_at`` wake only for
signals registered with a reoffer. budget-claude's RED is a forward-looking
burn forecast that is dispositioned and rechecked, so a registration without
reoffer left those dispositions with no wake at their deadline: a 2.2 h lapse
flapped signal-coverage RED on 2026-11-20. Every other budget signal carries
3600; this matches them.
"""


def rows_from_engine_budget(home, now):
    """Rows for ``evaluate`` from the producer's engine/budget.json (the one budget source since the 2026-11-12 cutover).

    The verdict keeps the pre-cutover rule: LOW on a spent window (used > 0.9), RUNS OUT BEFORE RESET when the burn ETA
    lands before the reset, otherwise ok. A forecast on a window at or below ``BURN_FORECAST_MIN_USED`` is a short-span
    extrapolation and stays ok. An unreadable file, a malformed account or a stale observation becomes an error row or
    a non-FRESH row, so ``evaluate`` reports UNKNOWN.
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
            elif round(used, 6) <= BURN_FORECAST_MIN_USED:
                # round(): engine/budget.json stores 5% as 0.050000000000000044, which must not
                # clear the short-span guard by float noise (2026-11-21 codex-a 7d RED at 1.6 h elapsed).
                verdict = "ok"
            elif ((account, window) not in PROJECTION_ACCEPTED
                  and isinstance(eta, (int, float)) and not isinstance(eta, bool) and now + eta < reset):
                verdict = "RUNS OUT BEFORE RESET"
            else:
                verdict = "ok"
            rows.append(dict(account=account, window=window, used=used, reset=reset,
                             state="FRESH" if fresh else "STALE", verdict=verdict))
    return rows

def publish(home, rows, *, now):
    """Observe the Claude verdict; return it with every supported provider's verdict.

    Only budget-claude is a registered signal. codex-a's forecast stays in the
    returned record and the household budget view rather than changing wake
    routing: it has no subscriber, and an hourly reoffer on a weekly forecast
    would wake a mind daily until the 7d reset. The red lane is dispositioned by
    the budget owner and surfaced by signal-coverage's dark-provider-verdict.
    """
    results = {account: dict(zip(("state", "reason"), evaluate(rows, account, now=now)))
               for account in SIGNALS}
    state, reason = results["claude"]["state"], results["claude"]["reason"]
    name = "budget-claude"
    reg = signal_store.registry(Path(home))
    if name not in reg:
        signal_store.register(Path(home), name, max_age=900, subscribers=[], reoffer=REOFFER_S)
    elif "reoffer" not in reg[name]:
        # Self-heal a registration made before reoffer existed. register() replaces the
        # whole entry, so carry the existing subscribers and wake_on forward: dropping them
        # would silently remove the senses/steward wake triggers signal-coverage relies on.
        entry = reg[name]
        signal_store.register(Path(home), name, max_age=entry.get("max_age", 900),
                              subscribers=entry.get("subscribers", []), reoffer=REOFFER_S,
                              wake_on=entry.get("wake_on", "change"))
    source = "existing-budget-view:claude"
    if state == "UNKNOWN":
        signal_store.observe_unknown(Path(home), name, now=now, reason=reason, source=source)
    else:
        # Carry the verdict's cause so subscribers can see which window triggered it.
        signal_store.observe(Path(home), name, state, now=now, source=source, reason=reason)
    # Cadence beat: observe_unknown never touches .seen (a failed input is not fresh evidence),
    # so without this a live producer declaring UNKNOWN looks like a missed cadence to coverage.
    signal_store.beat(Path(home), name)
    return dict(results["claude"], accounts=results)
