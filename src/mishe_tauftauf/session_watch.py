"""Owned session detector, independent of tmux panes and keeper unit names."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from .observations import validate_home


def assess(current: dict, previous: dict | None, now: float) -> dict:
    changes = []
    monitoring_ok = False
    if isinstance(previous, dict):
        stamp = previous.get("observed_at")
        history = previous.get("changes")
        if (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
                and 0 <= now - stamp <= 20 and isinstance(history, list)
                and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                        and 0 <= x <= now for x in history)):
            monitoring_ok = True
            changes = [x for x in history if now - x < 3600]
            if (previous.get("identity") and current.get("identity")
                    and previous["identity"] != current["identity"]):
                changes.append(now)
    state, reason = "GREEN", "owned session, panes and dashboard live"
    if not monitoring_ok:
        state, reason = "UNKNOWN", "monitor evidence missing, malformed, future or stale"
    if current.get("available") is not True:
        state, reason = "RED", "owned session missing or ownership unavailable"
    elif current.get("panes_live") is None:
        state, reason = "UNKNOWN", "pane liveness check failed"
    elif current.get("panes_live") is not True:
        state, reason = "RED", "resident pane missing or dead"
    elif current.get("dashboard_live") is not True:
        state, reason = "UNKNOWN", "operator dashboard missing, stale or broken"
    elif changes:
        state, reason = "RED", "session identity changed within the last hour"
    result = {**current, "observed_at": now, "state": state, "reason": reason, "changes": changes}
    if not monitoring_ok:
        result["invalid_previous"] = previous
    return result


def observe(home: Path, session: str) -> dict:
    def run(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=5)
    owner = run(["tmux", "show-option", "-qv", "-t", session, "@mishe-tauftauf-home"])
    identity = run(["tmux", "display-message", "-p", "-t", session, "#{pid}:#{session_id}:#{session_created}"])
    panes = run(["tmux", "list-panes", "-s", "-t", session, "-F", "#{pane_dead}"])
    dashboard = run([str(home / "bin/mishe-tauftauf"), "--home", str(home),
                     "pain", "read", "operator", "--launcher", "dashboard"])
    pane_states = panes.stdout.splitlines()
    return {"identity": identity.stdout.strip() if identity.returncode == 0 else None,
            "available": owner.returncode == 0 and owner.stdout.strip() == str(home.resolve())
                         and identity.returncode == 0 and bool(identity.stdout.strip()),
            "panes_live": (None if panes.returncode != 0 else
                           len(pane_states) >= 20 and all(x == "0" for x in pane_states)),
            "dashboard_live": dashboard.returncode == 0}


def notify(message: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.sendto(message.encode(), address)


def route(home: Path, row: dict) -> None:
    path = home / "health/session-watch-alert.md"
    path.write_text("Session watcher " + row["state"] + ": " + row["reason"] +
                    "\nEvidence: health/session-watch.json and session-watch-events.jsonl. "
                    "Resolver health: inspect actual session and launcher; bounded action "
                    "read journal + current session ownership; disposition within 120 seconds. "
                    "Recovery is not causal resolution.\n")
    subprocess.run([str(home / "bin/mishe-tauftauf"), "--home", str(home), "wall", "dm",
                    "--source", "session-watch", "--to", "health", "--file", str(path)],
                   check=True, timeout=15)


def run(home: Path, session: str) -> None:
    home = validate_home(home)
    path = home / "health/session-watch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    last_route = 0.0
    last_key = None
    routed_key = None
    route_failed = False
    notify("READY=1")
    while True:
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            if path.exists():
                # Preserve undecodable evidence before publishing the failure.
                archive = home / "artifacts" / ("session-watch-invalid-" + str(time.time_ns()) + ".json")
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(path.read_bytes())
            previous = None
        try:
            current = observe(home, session)
            row = assess(current, previous, time.time())
        except (OSError, subprocess.SubprocessError) as exc:
            row = {"state": "UNKNOWN", "reason": "observation failed: " + str(exc),
                   "observed_at": time.time(), "identity": None, "changes": []}
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(row) + "\n")
        temp.replace(path)
        # A slow observation (up to 20s) and alert (up to 15s) each get a
        # heartbeat; their budgets must never combine past WatchdogSec=30.
        notify("WATCHDOG=1\nSTATUS=" + row["state"] + " " + row["reason"])
        key = (row["state"], row["reason"])
        if key != last_key:
            with (home / "health/session-watch-events.jsonl").open("a") as log:
                log.write(json.dumps(row) + "\n")
            print(row, flush=True)
        # One alert per (state, reason) episode: a persistent RED is a single
        # condition whose evidence stays durable in health/session-watch.json,
        # session-watch-events.jsonl and the alert file, so re-sending identical
        # text every 120s only multiplied open resolver DMs (the 13:41:11Z
        # episode produced 12 indistinguishable DMs by 14:00Z). A failed route
        # still retries at the bounded 120s interval so a broken transport
        # fails visibly instead of silently dropping the condition.
        if row["state"] != "GREEN" and (key != routed_key or
                                        (route_failed and time.monotonic() - last_route >= 120)):
            last_route = time.monotonic()
            try:
                route(home, row)
                route_failed = False
            except (OSError, subprocess.SubprocessError) as exc:
                route_failed = True
                print("UNKNOWN session watcher routing failed: " + str(exc), flush=True)
            routed_key = key
        last_key = key
        notify("WATCHDOG=1\nSTATUS=" + row["state"] + " " + row["reason"])
        time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    run(args.home.resolve(), args.session)
