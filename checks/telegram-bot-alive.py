#!/usr/bin/env python3
"""Detector for the fine-tuned Telegram bot's long-poll loop.

Exit codes (duty contract): 0 GREEN, 2 RED, 3 cannot tell.

GREEN:  ilya-telegram-bot.service is active and the bot's heartbeat is fresh.
RED:    the unit is not active, or the poll loop stopped writing a fresh heartbeat.
UNKNOWN: systemd or the heartbeat cannot be read at all.

A RED gets at most RESTART_MAX restarts in RESTART_WINDOW, recorded in the heal
state file. The check never reads or prints the bot token; the heartbeat the bot
writes contains counters only.

Usage: telegram-bot-alive.py --home SITE_HOME [--heartbeat PATH] [--state PATH]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

UNIT = "ilya-telegram-bot.service"
MAX_HEARTBEAT_AGE = 180
RESTART_WINDOW = 30 * 60
RESTART_MAX = 2
SHOW_PROPS = ("ActiveState", "MainPID", "Result", "ActiveEnterTimestamp")


def unit_state(unit=UNIT, runner=subprocess.run):
    """Read the unit from systemd; raise OSError when systemd cannot be read."""
    result = runner(
        ["systemctl", "--user", "show", unit]
        + [arg for prop in SHOW_PROPS for arg in ("-p", prop)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "systemctl show failed")
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def read_heartbeat(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "ts" not in payload or "cycles" not in payload:
        raise ValueError("heartbeat has no ts/cycles")
    return payload


def read_heal(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or not isinstance(state.get("restarts"), list):
        state = {"restarts": []}
    return state


def write_heal(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def evaluate(state: dict, heartbeat: dict | None, now: float) -> tuple[int, str]:
    """Pure verdict from a systemd snapshot and a heartbeat reading."""
    if state.get("ActiveState") != "active" or not state.get("MainPID", "0").isdigit() \
            or state["MainPID"] == "0":
        return 2, f"unit {UNIT} is not active ({state.get('ActiveState', 'unknown')})"
    if heartbeat is None:
        return 3, "heartbeat missing or unreadable while the unit is active"
    age = now - float(heartbeat["ts"])
    if age > MAX_HEARTBEAT_AGE:
        return 2, f"poll loop heartbeat is {age:.0f}s old (limit {MAX_HEARTBEAT_AGE}s)"
    # A fresh startup heartbeat can legitimately show zero cycles: the first
    # getUpdates call blocks for up to 10 s before the first cycle is written.
    return 0, (f"unit active, heartbeat {age:.0f}s old, "
               f"{heartbeat['cycles']} poll cycles, {heartbeat.get('replies', 0)} replies")


def restart_budget(state: dict, now: float) -> int:
    recent = [t for t in state.get("restarts", [])
              if isinstance(t, (int, float)) and now - t < RESTART_WINDOW]
    return max(0, RESTART_MAX - len(recent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--heartbeat", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--unit", default=UNIT)
    args = parser.parse_args()

    heartbeat_path = args.heartbeat or args.home / "health" / "ilya-telegram-bot.json"
    heal_path = args.state or args.home / "health" / "ilya-telegram-bot-heal.json"
    now = time.time()

    try:
        state = unit_state(args.unit)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"VERDICT: UNKNOWN cannot read systemd: {exc}")
        return 3

    try:
        heartbeat = read_heartbeat(heartbeat_path)
    except (OSError, ValueError, TypeError, KeyError):
        heartbeat = None

    code, reason = evaluate(state, heartbeat, now)
    heal = read_heal(heal_path)

    if code == 2 and restart_budget(heal, now) > 0:
        heal.setdefault("restarts", []).append(now)
        write_heal(heal_path, heal)
        print(f"HEAL restarting {args.unit}: {reason}")
        try:
            subprocess.run(["systemctl", "--user", "restart", args.unit],
                           capture_output=True, text=True, timeout=30, check=True)
            time.sleep(2)
            state = unit_state(args.unit)
            try:
                heartbeat = read_heartbeat(heartbeat_path)
            except (OSError, ValueError, TypeError, KeyError):
                heartbeat = None
            code, reason = evaluate(state, heartbeat, time.time())
            reason = "after bounded restart: " + reason
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"HEAL restart failed: {exc}")
            code, reason = 2, f"restart failed: {exc}"
    elif code == 2:
        reason += " (restart budget spent)"

    verdict = {0: "GREEN", 2: "RED", 3: "UNKNOWN"}[code]
    print(f"VERDICT: {verdict} {reason}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
