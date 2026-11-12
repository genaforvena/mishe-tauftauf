"""Producer for the one-source budget file (docs/budget-json-contract.md, schema 1).

This producer writes the `accounts` section (account windows from the engine status
(quota.json shape) and the native Claude tap), the `routing` section (the router's own
assess() over those accounts), the `cooling` section (the router's own cooldown state), and the
ledger sections `free_models`, `paid_models`, `unclassified_models` (ledger candidates absent from
routes.json, UNKNOWN) and `per_mind` (outcome counts, pending selects and the last outcome from
routing-ledger.jsonl), plus a `ledger` meta block (events read, malformed rows skipped, newest event
time), and per fresh window `burn_h` (used fraction per hour) and `eta_s` (seconds to 100% at that burn) from the
rate histories. Candidate token counts are a later slice. Pure functions here; file IO is limited to the atomic writer and the history reads.

Fail-closed rules from the contract: a window is FRESH only when its own observation is
within MAX_AGE_S and its reset lies in the future; any window whose fields are not all
numeric is left out (never read as 0%); a missing or unreadable account is UNREADABLE.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from mishe_tauftauf.quota_history_text import read_jsonl_rows, split_quota_label

SCHEMA = 1
MAX_AGE_S = 900.0
PRODUCER = "src/mishe_tauftauf/budget_producer.py"
WINDOW_TAP_KEYS = {"5h": "five_hour", "7d": "seven_day"}
SOURCE_STATES = ("FRESH", "STALE", "UNKNOWN", "UNATTRIBUTED", "UNREADABLE")
BURN_HISTORY_S = 6 * 3600.0   # history considered for burn; older points are dropped
BURN_MIN_SPAN_S = 1500.0      # a burn needs at least this much observed span
BURN_RESET_TOL_S = 120.0      # points from a previous window (different reset) are not the same window
WINDOW_DURATION_S = {"5h": 5 * 3600.0, "7d": 7 * 24 * 3600.0}  # elapsed-window fallback needs the window length
BURN_ELAPSED_MIN_H = 0.25     # the elapsed-window estimate needs this much elapsed window time (legacy budget_view rule)
CLAUDE_HISTORY = "claude-rate-history.jsonl"   # rows: {at, five_hour: [pct, reset], seven_day: [pct, reset]}
ENGINE_HISTORY = "quota-history.jsonl"         # rows: {at, q: {"acct/window": [remaining, reset]}}


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def window_fresh_at(observed_at, reset_at, now) -> bool:
    """The one freshness rule: observed within MAX_AGE_S of `now` (not from the future) and reset still ahead.
    The router re-applies this at decision time, so a label written earlier cannot outlive its window."""
    return _num(observed_at) and _num(reset_at) and now - MAX_AGE_S <= observed_at <= now + 5 and reset_at > now


def _state(observed_at, reset_at, now, source_state="FRESH") -> str:
    if source_state != "FRESH":
        return source_state
    return "FRESH" if window_fresh_at(observed_at, reset_at, now) else "STALE"


def _window(used, reset_at, observed_at, source, now, source_state="FRESH") -> dict:
    return {
        "used": used,
        "remaining": 1 - used,
        "reset_at": reset_at,
        "source": source,
        "observed_at": observed_at,
        "state": _state(observed_at, reset_at, now, source_state),
    }


def native_claude_windows(tap, now, malformed: list | None = None) -> dict:
    """Claude windows from the native tap. used_percentage is 0..100; a window with any
    non-numeric field is omitted (a null resets_at once crashed the reader)."""
    windows = {}
    if not isinstance(tap, dict) or tap.get("state") != "FRESH" or tap.get("source") != "claude-native-usage":
        return windows
    for window, key in WINDOW_TAP_KEYS.items():
        x = tap.get(key)
        if not isinstance(x, dict):
            continue
        used_pct, reset_at, observed_at = x.get("used_percentage"), x.get("resets_at"), x.get("observed_at")
        if not all(_num(v) for v in (used_pct, reset_at, observed_at)):
            if malformed is not None:
                malformed.append(window)
            continue
        windows[window] = _window(used_pct / 100, reset_at, observed_at, "claude-native-usage", now)
    return windows


def _source_state(value) -> str:
    """The engine's own state label, or UNKNOWN when it is absent or not a label the engine reports."""
    return value if value in SOURCE_STATES else "UNKNOWN"


def engine_windows(account_row, now, malformed: list | None = None) -> dict:
    """Windows from one engine status account row: {state, observed_at?, windows: {w: {remaining, reset_at, state?}}}.

    Provenance is the engine's own observation time: a window's `observed_at`, else the account's
    (the collector writes the engine's fetched_at there). A row with no observation time is dropped, never
    stamped with the collection time. A window is FRESH only when the engine labels it FRESH (and its
    account too); any other label, or a missing label, is carried as that non-FRESH state."""
    windows = {}
    if not isinstance(account_row, dict):
        return windows
    account_state = _source_state(account_row.get("state"))
    for window, v in (account_row.get("windows") or {}).items():
        if not isinstance(v, dict):
            if malformed is not None:
                malformed.append(window)
            continue
        remaining, reset_at = v.get("remaining"), v.get("reset_at")
        observed_at = v.get("observed_at", account_row.get("observed_at"))
        if not all(_num(x) for x in (remaining, reset_at, observed_at)):
            if malformed is not None:
                malformed.append(window)
            continue
        source = _source_state(v.get("state")) if account_state == "FRESH" else account_state
        windows[window] = _window(1 - remaining, reset_at, observed_at, "engine-usage", now, source)
    return windows


def recent_burn(points, reset_at) -> float | None:
    """Pure: burn per hour (used fraction) from (at, used, reset) points of one window.
    Only points whose reset matches this window count; the reading must span BURN_MIN_SPAN_S.
    Negative deltas (a reset inside the span) are not burn. None when there is no honest reading."""
    pts = sorted(
        (at, used) for at, used, reset in points
        if _num(at) and _num(used) and _num(reset) and abs(reset - reset_at) < BURN_RESET_TOL_S
    )
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < BURN_MIN_SPAN_S:
        return None
    (t0, u0), (t1, u1) = pts[0], pts[-1]
    return max(u1 - u0, 0.0) / ((t1 - t0) / 3600.0)


def _history_rows(path: Path, now: float) -> list:
    """Rows of a JSONL history within BURN_HISTORY_S of now. Unreadable or undecodable lines are skipped."""
    return [
        row for row in read_jsonl_rows(path)
        if _num(row.get("at")) and now - BURN_HISTORY_S <= row["at"] <= now + 5
    ]


def load_burn_history(home: Path, now: float) -> dict:
    """{(account, window): [(at, used, reset), ...]} from the claude tap and engine quota histories.
    The claude windows are keyed as ("claude", "5h"|"7d"); engine windows as (account, window)."""
    points: dict = {}
    budget_dir = home / "operator" / "budget"
    for row in _history_rows(budget_dir / CLAUDE_HISTORY, now):
        for window, key in WINDOW_TAP_KEYS.items():
            v = row.get(key)
            if isinstance(v, list) and len(v) == 2:
                points.setdefault(("claude", window), []).append((row["at"], _pct_used(v[0]), v[1]))
    for row in _history_rows(budget_dir / ENGINE_HISTORY, now):
        q = row.get("q")
        if not isinstance(q, dict):
            continue
        for label, v in q.items():
            account, window = split_quota_label(label)
            if account and isinstance(v, list) and len(v) == 2:
                points.setdefault((account, window), []).append((row["at"], _engine_used(v[0]), v[1]))
    return points


def _pct_used(x):
    return x / 100 if _num(x) else None


def _engine_used(remaining):
    return 1 - remaining if _num(remaining) else None


def elapsed_window_burn(used, reset_at, window: str, now: float) -> float | None:
    """Pure: burn per hour estimated from the elapsed part of a known-length window (used / hours elapsed).
    Used only when the rate history has no honest reading. None when the window length is unknown, nothing is
    used yet, or less than BURN_ELAPSED_MIN_H of the window has elapsed. This is an estimate, not a measurement:
    the caller tags it burn_source="window-elapsed"."""
    duration = WINDOW_DURATION_S.get(window)
    if duration is None or not all(_num(v) for v in (used, reset_at, now)) or used <= 0:
        return None
    elapsed_h = (duration - (reset_at - now)) / 3600.0
    if elapsed_h < BURN_ELAPSED_MIN_H:
        return None
    return used / elapsed_h


def _attach_burn(windows: dict, account: str, burn: dict | None, now: float) -> None:
    """Add burn_h, eta_s and burn_source to each FRESH window. Non-fresh windows get None: a stale reading is no burn.
    burn_source is "history" (rate history reading), "window-elapsed" (fallback estimate) or None (no burn)."""
    for window, w in windows.items():
        burned, source = None, None
        if w["state"] == "FRESH":
            if burn is not None:
                burned = recent_burn(burn.get((account, window), []), w["reset_at"])
                source = "history" if burned is not None else None
            if burned is None:
                burned = elapsed_window_burn(w["used"], w["reset_at"], window, now)
                source = "window-elapsed" if burned is not None else None
        w["burn_h"] = burned
        w["burn_source"] = source
        w["eta_s"] = (1 - w["used"]) / burned * 3600.0 if burned else None


def build_accounts(status: dict | None, tap, now: float, burn: dict | None = None) -> dict:
    """Accounts section. `status` is the engine status dict (or None if unreadable).
    `burn` is the history from load_burn_history; without it burn_h and eta_s are None."""
    accounts = {}
    engine = (status or {}).get("accounts") if isinstance(status, dict) else None
    for name, row in (engine or {}).items():
        if not isinstance(row, dict):
            continue
        bad: list = []
        windows = engine_windows(row, now, bad)
        accounts[name] = {"provider": row.get("provider"), "windows": windows}
        if bad:
            accounts[name]["malformed"] = bad
    bad = []
    claude = native_claude_windows(tap, now, bad)
    accounts["claude"] = {"provider": "claude-code", "windows": claude}
    if bad:
        accounts["claude"]["malformed"] = bad
    for name, row in accounts.items():
        _attach_burn(row["windows"], name, burn, now)
        states = {w["state"] for w in row["windows"].values()}
        row["state"] = "UNREADABLE" if not row["windows"] or row.get("malformed") else "FRESH" if states == {"FRESH"} else "STALE"
    return accounts


def build_budget(status: dict | None, tap, now: float, burn: dict | None = None) -> dict:
    return {
        "schema": SCHEMA,
        "produced_at": now,
        "producer": PRODUCER,
        "accounts": build_accounts(status, tap, now, burn),
        "unknowns": [] if status is not None else ["engine status unavailable"],
    }


def build_routing(home: Path, payload: dict, now: float) -> dict:
    """The `routing` section: the router's own assess() over this payload's accounts and the
    router's cooling and running state, so the view cannot disagree with the next selection."""
    from . import engine_router as er

    try:
        routes = er.load_routes(home)
        state = er._load_state(home)
    except ValueError as exc:
        return {"state": "RED", "reason": f"routes file invalid: {exc}", "roles": {}, "cooled": []}
    except OSError as exc:
        return {"state": "UNKNOWN", "reason": f"routes or routing state unreadable: {exc}", "roles": {}, "cooled": []}
    running = None
    if routes is not None:
        running = er.running_candidates(home, now_ms=now * 1000,
                                        horizon_s=routes.get("hard_timeout_s", er.DEFAULT_HARD_TIMEOUT_S))
    return er.assess(routes, er.status_from_payload(payload, now), state["cooling"], now * 1000, running=running)


def build_cooling(home: Path, now: float) -> dict:
    """The `cooling` section: the router's own cooling entries (routing state, keyed as the router
    keys them), so the view reads the same cooldowns the next selection will honour. An entry
    whose `until` is not a finite number is dropped, never read as cooled-forever or never-cooled."""
    from . import engine_router as er

    try:
        cooling = er._load_state(home)["cooling"]
    except (OSError, ValueError):
        return {}
    out = {}
    for key, until in cooling.items():
        if not _num(until):
            continue
        out[key] = {"until": until, "active": until > now * 1000}
    return out


LEDGER_WINDOWS = (("1h", 3600.0), ("24h", 86400.0))
TOKEN_NOTE = ("per-candidate out/in tokens unattributed: state.json buckets are keyed provider|model, "
              "not router candidate; null until attributed")


def _ledger_tally(events, now: float) -> dict:
    """Pure: outcome counts per (role, candidate key) for each window, the latest outcome, the
    pending selects (a select with no closing outcome or fallback for the same wake, role and key),
    and the newest ledger timestamp in epoch ms (the read watermark, not the producer clock).

    Every outcome counts, timeouts and no-yield included, because that is where burn is highest.
    Only `outcome` rows count as turns; a `fallback` closes a select without counting a turn.
    An outcome whose timestamp cannot be read is not counted and is returned as `unreadable`.
    A select, outcome or fallback row without a role or key string cannot be attributed to any candidate;
    it is not counted and is returned as `malformed` (partial evidence, never folded into another slot)."""
    from .router_text import iso_ms

    now_ms = now * 1000
    counts: dict = {}
    last: dict = {}
    open_sel: dict = {}
    unreadable = 0
    malformed = 0
    watermark = None
    for ev in events:
        at = iso_ms(ev.get("at"))
        if at is not None and (watermark is None or at > watermark):
            watermark = at
        role, key, wake = ev.get("role"), ev.get("key"), ev.get("wake")
        kind = ev.get("event")
        if kind not in ("select", "outcome", "fallback"):
            continue
        if not isinstance(role, str) or not isinstance(key, str):
            malformed += 1
            continue
        ident = (wake, role, key)
        if kind == "select":
            open_sel[ident] = open_sel.get(ident, 0) + 1
            continue
        if open_sel.get(ident, 0) > 0:
            open_sel[ident] -= 1
        if kind == "fallback":
            continue
        if at is None:
            unreadable += 1
            continue
        slot = (role, key)
        if slot not in last or at >= last[slot][0]:
            last[slot] = (at, {"outcome": ev.get("outcome"), "at": ev.get("at")}, role)
        window = counts.setdefault(slot, {name: 0 for name, _ in LEDGER_WINDOWS})
        for name, span in LEDGER_WINDOWS:
            if now_ms - span * 1000 <= at <= now_ms + 5000:
                window[name] += 1
    pending: dict = {}
    for (_wake, role, key), n in open_sel.items():
        if n > 0:
            pending[(role, key)] = pending.get((role, key), 0) + n
    return {"counts": counts, "last": last, "pending": pending, "watermark": watermark, "unreadable": unreadable,
            "malformed": malformed}


def _ledger_row(tally: dict, slot, meta: dict, present: bool, watermark_ms) -> dict:
    """One model or per-role row. Counts are null when the ledger is missing (UNKNOWN, never 0)."""
    counts = tally["counts"].get(slot, {name: 0 for name, _ in LEDGER_WINDOWS})
    last = tally["last"].get(slot)
    row = dict(meta)
    for name, _ in LEDGER_WINDOWS:
        row[f"turns_{name}"] = counts[name] if present else None
        row[f"out_{name}"] = None
        row[f"in_{name}"] = None
    row["last_outcome"] = last[1] if last and present else None
    row["pending"] = tally["pending"].get(slot, 0) if present else None
    row["observed_at"] = watermark_ms / 1000 if present and watermark_ms is not None else None
    return row


def build_ledger(home: Path, now: float) -> dict:
    """The ledger sections (`free_models`, `paid_models`, `per_mind`) from routing-ledger.jsonl and the
    routes file. Free candidates (`free: true`, no account) never sum into a paid account; a candidate
    absent from the routes is kept with `in_routes: false`, not dropped. Token fields are null until
    candidate-level attribution exists (see TOKEN_NOTE)."""
    from . import engine_router as er
    from .router_text import ledger_events

    unknowns: list = []
    try:
        routes = er.load_routes(home)
    except (OSError, ValueError) as exc:
        routes = None
        unknowns.append(f"routes unreadable, free/paid split unknown: {exc}")
    path = er._dir(home) / er.LEDGER
    present = path.exists()
    if not present:
        unknowns.append("routing ledger missing: ledger sections are UNKNOWN")
    skipped: list = []
    events = list(ledger_events(path, skipped)) if present else []
    tally = _ledger_tally(events, now)
    if skipped:
        unknowns.append(f"{len(skipped)} ledger row(s) not a JSON object skipped (first at line {skipped[0]}); "
                        "counts exclude them")
    if tally["unreadable"]:
        unknowns.append(f"{tally['unreadable']} ledger outcome row(s) with unreadable timestamps not counted")
    if tally["malformed"]:
        unknowns.append(f"{tally['malformed']} ledger row(s) with no role or key not attributed; counts exclude them")
    if present:
        unknowns.append(TOKEN_NOTE)

    meta: dict = {}
    roles: dict = {}
    if routes is not None:
        for role, cands in (routes.get("roles") or {}).items():
            for c in cands:
                key = er.candidate_key(c)
                account = c.get("account")
                meta[key] = {"free": bool(c.get("free")), "account": account, "in_routes": True}
                roles.setdefault(role, set()).add(key)
    retired_keys = {er.candidate_key(c) for c in (routes or {}).get("retired", [])}
    unrouted: set = set()
    for (role, key) in list(tally["counts"]) + list(tally["pending"]):
        if key not in meta and key not in retired_keys:
            unrouted.add(key)
        roles.setdefault(role, set()).add(key)
    if unrouted:
        # Not in routes.json: free or paid cannot be read from the file, so neither classified section
        # claims it. Its totals and last outcome go to `unclassified_models` (UNKNOWN, in_routes false),
        # and its per-role rows stay in per_mind; the unknown names it.
        unknowns.append("ledger candidates absent from routes, not classified: " + ", ".join(sorted(unrouted)))

    wm = tally["watermark"]
    free_models, paid_models, unclassified_models = {}, {}, {}
    for key, m in sorted(meta.items()):
        if m["free"]:
            base = {"free": True, "account": None, "in_routes": True, "verdict": "FREE",
                    "note": "no limit known"}
            free_models[key] = _model_row(tally, key, base, present, wm)
        else:
            base = {"free": False, "account": m["account"], "in_routes": True, "verdict": None}
            if m["account"] is None:
                base["note"] = "subscription safety net: no account window is read"
            paid_models[key] = _model_row(tally, key, base, present, wm)
    for key in sorted(unrouted):
        base = {"free": None, "account": None, "in_routes": False, "verdict": "UNKNOWN",
                "note": "absent from routes.json: free or paid is not readable from the file"}
        unclassified_models[key] = _model_row(tally, key, base, present, wm)

    retired_models: dict = {}
    for key in sorted(retired_keys):
        base = {"free": None, "account": None, "in_routes": False, "verdict": "RETIRED",
                "note": "retired from routes.json: no role selects it"}
        retired_models[key] = _model_row(tally, key, base, present, wm)

    per_mind: dict = {}
    for role, keys in sorted(roles.items()):
        per_mind[role] = {}
        for key in sorted(keys):
            per_mind[role][key] = _ledger_row(tally, (role, key), {}, present, wm)
    # `newest_event_at` is the newest ledger event, not proof the collector ran or the ledger is complete:
    # an idle host and a failed writer both look like an old newest event. `produced_at` is the producer clock.
    ledger = {"present": present, "events": len(events),
              "malformed_skipped": len(skipped) + tally["malformed"],
              "newest_event_at": wm / 1000 if present and wm is not None else None,
              "produced_at": now}
    return {"free_models": free_models, "paid_models": paid_models, "unclassified_models": unclassified_models,
            "retired_models": retired_models, "per_mind": per_mind, "ledger": ledger, "ledger_unknowns": unknowns}


def _model_row(tally: dict, key: str, base: dict, present: bool, wm) -> dict:
    """A candidate's row across all roles: counts, pending and last outcome summed/latest over roles."""
    row = dict(base)
    slots = [s for s in set(tally["counts"]) | set(tally["pending"]) | set(tally["last"]) if s[1] == key]
    for name, _ in LEDGER_WINDOWS:
        total = sum(tally["counts"][s][name] for s in slots if s in tally["counts"]) if present else None
        row[f"turns_{name}"] = total
        row[f"out_{name}"] = None
        row[f"in_{name}"] = None
    row["pending"] = sum(tally["pending"].get(s, 0) for s in slots) if present else None
    latest = max((tally["last"][s] for s in slots if s in tally["last"]), default=None, key=lambda v: v[0])
    row["last_outcome"] = ({"role": latest[2], "outcome": latest[1]["outcome"], "at": latest[1]["at"]}
                           if latest and present else None)
    row["observed_at"] = wm / 1000 if present and wm is not None else None
    return row


def write_atomic(path: Path, payload: dict) -> None:
    """Write JSON to a temp file in the same directory, then replace; no partial reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, sort_keys=True, indent=1, allow_nan=False)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path):
    """Approved read (state_files.read_json); missing or unreadable input is None, never a guess."""
    from .state_files import read_json

    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def main(home: Path, out: Path | None = None) -> int:
    """Produce the accounts section from the site home. Dry-run (stdout) unless `out` is given.
    The argv entrypoint is `mishe budget-produce` in runtime_cli, which owns argument parsing."""
    import time

    now = time.time()
    status = _read_json(home / "operator" / "budget" / "quota.json")
    tap = _read_json(home / "operator" / "budget" / "claude-native-quota.json")
    payload = build_budget(status, tap, now, load_burn_history(home, now))
    payload["routing"] = build_routing(home, payload, now)
    payload["cooling"] = build_cooling(home, now)
    ledger = build_ledger(home, now)
    payload["free_models"] = ledger["free_models"]
    payload["paid_models"] = ledger["paid_models"]
    payload["unclassified_models"] = ledger["unclassified_models"]
    payload["retired_models"] = ledger["retired_models"]
    payload["per_mind"] = ledger["per_mind"]
    payload["ledger"] = ledger["ledger"]
    payload["unknowns"] = payload["unknowns"] + ledger["ledger_unknowns"]
    if out:
        write_atomic(Path(out), payload)
    else:
        print(json.dumps(payload, sort_keys=True, indent=1, allow_nan=False))
    return 0
