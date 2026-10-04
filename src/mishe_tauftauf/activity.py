"""Observed mind activity, delivery staleness, and quiet/ended notices to health."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
from pathlib import Path
import subprocess
import sys
import time

from .feed import Feed
from .post_check import _save
from .wall import settings, message

ROLES = frozenset({"genome", "witness", "senses", "discover", "health", "docs", "research-methods"})


def roles(home: Path) -> frozenset[str]:
    """Roles whose activity counts: the built-in set plus site-declared residents.

    A site can add a resident beyond the built-in defaults — a tmux window, an
    executable ``minds/<slug>`` launcher and a ``charters/<slug>.md`` charter.
    Counting only the hard-coded set would leave that resident's activity and
    silence invisible on every pane. ``plant`` preserves extra charters and
    launchers it did not generate, so a declared resident stays monitored across
    a replant; an unreadable site falls back to the built-in set.
    """
    declared = set()
    try:
        for path in (home / "charters").glob("*.md"):
            if (home / "minds" / path.stem).is_file():
                declared.add(path.stem)
    except OSError:
        return ROLES
    return ROLES | declared


@contextmanager
def lock(home):
    with (home / ".silence.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def threshold(config):
    seconds = float(config.get("silence_seconds", 600))
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("silence_seconds must be finite and nonnegative; zero disables alerts")
    return seconds


def configure(home, seconds):
    with lock(home):
        config = settings(home)
        config["silence_seconds"] = seconds
        threshold(config)
        _save(home / "coordination-mode.json", config)


def observe(home, *, now=None, entries=None):
    now = now or datetime.now(timezone.utc)
    config = settings(home)
    seconds = threshold(config)
    monitored = roles(home)
    candidates = [(datetime.fromisoformat(config.get("started", now.isoformat()).replace("Z", "+00:00")), "plant start")]
    for entry in entries if entries is not None else Feed(home).entries():
        source = entry.source.removeprefix("mind/")
        if source in monitored or (entry.source == "seed" and entry.body.startswith("seed yield ")):
            candidates.append((datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00")),
                               f"chat.log {entry.sequence} {entry.source}"))
    for path in (home / "walls").glob("*.md"):
        if path.stem in monitored:
            candidates.append((datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), f"walls/{path.name}"))
    at, evidence = max(candidates)
    idle = max(0, (now - at).total_seconds())
    armed = not config.get("paused")
    within = not config.get("until") or now < datetime.fromisoformat(config["until"].replace("Z", "+00:00"))
    if not armed or not seconds:
        state = "DISABLED"
    elif not within:
        state = "ENDED"
    elif idle >= seconds:
        state = "SILENT"
    else:
        state = "OK"
    return {"last_action": at.isoformat(), "evidence": evidence, "idle_seconds": idle,
            "threshold_seconds": seconds, "state": state, "silent": state == "SILENT"}


def line(home, *, now=None, entries=None):
    reading = observe(home, now=now, entries=entries)
    return (f"ACTIVITY: {reading['state']} — last mind action {reading['idle_seconds']:.0f}s ago; "
            f"silence threshold {reading['threshold_seconds']:g}s; {reading['evidence']}")


def delivery(home, *, repo=None, now=None):
    """Seconds since the last Git commit and the last applied patch activation."""
    now = now or datetime.now(timezone.utc)
    facts = {"last_commit_seconds": None, "last_activation_seconds": None}
    try:
        result = subprocess.run(["git", "-C", str(repo or home.parent), "log", "-1", "--format=%ct"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip().isdigit():
            committed = datetime.fromtimestamp(int(result.stdout.strip()), timezone.utc)
            facts["last_commit_seconds"] = max(0, (now - committed).total_seconds())
    except (OSError, subprocess.SubprocessError):
        pass
    stamps = [path.stat().st_mtime for path in (home / "patches").glob("*-activate.log")]
    if stamps:
        facts["last_activation_seconds"] = max(0, now.timestamp() - max(stamps))
    return facts


def beat(home, state):
    """Record watcher liveness so a dead or muted watcher stays visible on every pane."""
    try:
        _save(home / "checks/silence-heartbeat.json",
              {"at": datetime.now(timezone.utc).isoformat(), "state": state})
    except OSError:
        pass


def notice(reading):
    if reading["state"] == "ENDED":
        return (f"Trial window ended: no observed mind action for {reading['idle_seconds']:.0f}s. "
                f"Last action: {reading['evidence']} at {reading['last_action']}. New wakes have stopped; "
                "inspect the live panes and delivery state, then decide whether to re-arm the window or report. "
                "This notice is not proof of failure.")
    return (f"Silence alert: no observed mind action for {reading['idle_seconds']:.0f}s "
            f"(threshold {reading['threshold_seconds']:g}s). Last action: {reading['evidence']} "
            f"at {reading['last_action']}. Read the live dashboards and actual mind/wake state, "
            "figure out whether work is ongoing or stalled, and make sure the local mesh is healthy. "
            "Planning may be quiet; this timer is not proof of failure. Preserve pending turns and prior effects.")


def check(home, *, now=None):
    """One shared-tape notice per unchanged quiet or ended episode; a notice is not activity."""
    with lock(home):
        reading = observe(home, now=now)
        reading["delivery"] = delivery(home, now=now)
        path = home / "checks/silence-watch.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        same = all(previous.get(key) == reading[key]
                   for key in ("last_action", "evidence", "threshold_seconds", "state"))
        if reading["state"] in {"SILENT", "ENDED"}:
            if same and previous.get("alert_sequence"):
                reading.update({key: previous[key] for key in ("alert_sequence", "dispatched", "last_attempt") if key in previous})
            else:
                alert = message(home, "silence-watch", "health", notice(reading))
                reading["alert_sequence"] = alert.sequence
        _save(path, reading)
        return reading


def wake_health(home, session):
    from . import seed, wall
    if not seed._mind_ready(session, "health"):
        return "held health mind busy; alert remains on shared tape"
    if seed._state(home, "health")[1] is not None:
        wall.retry(home, "health")
    return seed.tick(home, session, "health", 300)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--wake-health", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    if args.wake_health:
        print(wake_health(args.home, args.session), flush=True)
        return
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("interval must be positive and finite")
    while True:
        try:
            reading = check(args.home)
            beat(args.home, reading["state"])
            if reading.get("alert_sequence") and not reading.get("dispatched"):
                result = subprocess.run([sys.executable, "-m", "mishe_tauftauf.activity", "--home", str(args.home),
                    "--session", args.session, "--wake-health"], capture_output=True, text=True, timeout=15)
                output = result.stdout.strip()
                reading.update(dispatched=result.returncode == 0 and output.startswith("wake "),
                               last_attempt=output or result.stderr.strip())
                with lock(args.home):
                    _save(args.home / "checks/silence-watch.json", reading)
                print(f"silence alert {reading['alert_sequence']}: {reading['last_attempt']}", flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            beat(args.home, "UNKNOWN")
            print(f"UNKNOWN silence watch: {exc}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
