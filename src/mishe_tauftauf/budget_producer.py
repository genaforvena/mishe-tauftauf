"""Producer for the one-source budget file (docs/budget-json-contract.md, schema 1).

This slice produces the `accounts` section (account windows from the engine status
(quota.json shape) and the native Claude tap) and the `routing` section (the router's own
assess() over those accounts) and the `cooling` section (the router's own cooldown state).
Ledger sections (free_models, paid_models, per_mind) are later slices. Pure functions here; file IO
is limited to the atomic writer.

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

SCHEMA = 1
MAX_AGE_S = 900.0
PRODUCER = "src/mishe_tauftauf/budget_producer.py"
WINDOW_TAP_KEYS = {"5h": "five_hour", "7d": "seven_day"}
SOURCE_STATES = ("FRESH", "STALE", "UNKNOWN", "UNATTRIBUTED", "UNREADABLE")


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


def build_accounts(status: dict | None, tap, now: float) -> dict:
    """Accounts section. `status` is the engine status dict (or None if unreadable)."""
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
        states = {w["state"] for w in row["windows"].values()}
        row["state"] = "UNREADABLE" if not row["windows"] or row.get("malformed") else "FRESH" if states == {"FRESH"} else "STALE"
    return accounts


def build_budget(status: dict | None, tap, now: float) -> dict:
    return {
        "schema": SCHEMA,
        "produced_at": now,
        "producer": PRODUCER,
        "accounts": build_accounts(status, tap, now),
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
    payload = build_budget(status, tap, now)
    payload["routing"] = build_routing(home, payload, now)
    payload["cooling"] = build_cooling(home, now)
    if out:
        write_atomic(Path(out), payload)
    else:
        print(json.dumps(payload, sort_keys=True, indent=1, allow_nan=False))
    return 0
