"""Producer for the one-source budget file (docs/budget-json-contract.md, schema 1).

This first slice produces only the `accounts` section: account windows from the engine
status (quota.json shape) and the native Claude tap. Ledger sections (free_models,
paid_models, per_mind, cooling, routing) are later slices. Pure functions here; file IO
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


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _state(observed_at, reset_at, now) -> str:
    fresh = now - MAX_AGE_S <= observed_at <= now + 5 and reset_at > now
    return "FRESH" if fresh else "STALE"


def _window(used, reset_at, observed_at, source, now) -> dict:
    return {
        "used": used,
        "remaining": 1 - used,
        "reset_at": reset_at,
        "source": source,
        "observed_at": observed_at,
        "state": _state(observed_at, reset_at, now),
    }


def native_claude_windows(tap, now) -> dict:
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
            continue
        windows[window] = _window(used_pct / 100, reset_at, observed_at, "claude-native-usage", now)
    return windows


def engine_windows(account_row, now) -> dict:
    """Windows from one engine status account row: {windows: {w: {remaining, reset_at, observed_at?}}}."""
    windows = {}
    if not isinstance(account_row, dict):
        return windows
    for window, v in (account_row.get("windows") or {}).items():
        if not isinstance(v, dict):
            continue
        remaining, reset_at = v.get("remaining"), v.get("reset_at")
        observed_at = v.get("observed_at", account_row.get("observed_at"))
        if not all(_num(x) for x in (remaining, reset_at, observed_at)):
            continue
        windows[window] = _window(1 - remaining, reset_at, observed_at, "engine-usage", now)
    return windows


def build_accounts(status: dict | None, tap, now: float) -> dict:
    """Accounts section. `status` is the engine status dict (or None if unreadable)."""
    accounts = {}
    engine = (status or {}).get("accounts") if isinstance(status, dict) else None
    for name, row in (engine or {}).items():
        if not isinstance(row, dict):
            continue
        windows = engine_windows(row, now)
        accounts[name] = {"provider": row.get("provider"), "windows": windows}
    claude = native_claude_windows(tap, now)
    accounts["claude"] = {"provider": "claude-code", "windows": claude}
    for name, row in accounts.items():
        states = {w["state"] for w in row["windows"].values()}
        row["state"] = "UNREADABLE" if not row["windows"] else "FRESH" if states == {"FRESH"} else "STALE"
    return accounts


def build_budget(status: dict | None, tap, now: float) -> dict:
    return {
        "schema": SCHEMA,
        "produced_at": now,
        "producer": PRODUCER,
        "accounts": build_accounts(status, tap, now),
        "unknowns": [] if status is not None else ["engine status unavailable"],
    }


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
    if out:
        write_atomic(Path(out), payload)
    else:
        print(json.dumps(payload, sort_keys=True, indent=1, allow_nan=False))
    return 0
