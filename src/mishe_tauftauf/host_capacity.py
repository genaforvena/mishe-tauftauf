"""Independent host resource boundary and reversible optional-work admission.

Capacity is a measured resource state, never a claim that incidents are fixed.
This process runs outside the workload slice and never needs the chat locks.
"""
from __future__ import annotations

import argparse
import fcntl
import math
from pathlib import Path
import re
import time

from . import recovery_io as recovery


def decide(previous, observation, *, now, stable_seconds=120):
    old = previous or {}
    row = {"version": 1, "checked_at": now, "max_age": 20,
           "observation": observation, "managed_units": old.get("managed_units", []),
           "pending_stops": old.get("pending_stops", {}),
           "pending_starts": old.get("pending_starts", {}),
           "clear_since": None, "oom_delta": 0, "scope": "resource capacity; not functional restoration"}
    try:
        values = [observation[k] for k in ("memory_full", "io_full", "available_ratio", "current", "high", "max")]
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("invalid resource values")
        memory, io, available, current, high, maximum = values
        if not (0 < high < maximum and available <= 1 and memory <= 100 and io <= 100):
            raise ValueError("invalid resource bounds")
        count = observation["events"]["oom_kill"]
        if type(count) is not int or count < 0:
            raise ValueError("invalid OOM counter")
        before = old.get("observation") or {}
        row["oom_delta"] = max(0, count - before.get("events", {}).get("oom_kill", count))
        critical = current >= maximum * .9 or available < .06 or memory >= 25 or row["oom_delta"] > 0
        overloaded = critical or current >= high * .9 or available < .12 or memory >= 5 or io >= 40
        clear = current < high * .8 and available >= .18 and memory < 1 and io < 10
        contiguous = 0 <= now - old.get("checked_at", now) <= 20
        since = old.get("clear_since") if contiguous else None
        if clear and not overloaded:
            row["clear_since"] = now if since is None else since
        held = old.get("resource_limited", old.get("limited", False)) and not (
            row["clear_since"] is not None and now - row["clear_since"] >= stable_seconds)
        limited = overloaded or held
        state = "CRITICAL" if critical else "LIMITED" if limited else "NORMAL"
    except (KeyError, TypeError, ValueError):
        state, limited = "UNKNOWN", True
    row.update(state=state, limited=limited, resource_limited=limited,
               slots=0 if state == "CRITICAL" else 1 if limited else 4,
               cooldown=30 if limited else 5, render_timeout=30)
    return row


def status(root, *, now=None):
    now = time.time() if now is None else now
    try:
        row = recovery.read_json(Path(root) / "host-capacity.json")
        if (not isinstance(row, dict) or row.get("version") != 1
                or row.get("state") not in {"NORMAL", "LIMITED", "CRITICAL", "UNKNOWN"}
                or type(row.get("limited")) is not bool
                or type(row.get("slots")) is not int or not 0 <= row["slots"] <= 4
                or type(row.get("checked_at")) not in (int, float)
                or not 0 <= now - row["checked_at"] <= 20
                or row.get("cooldown") not in (5, 30)
                or row.get("render_timeout") != 30
                or (row["state"] == "NORMAL" and (row["limited"] or row["slots"] != 4))
                or (row["state"] == "CRITICAL" and row["slots"] != 0)
                or (row["state"] in {"UNKNOWN", "LIMITED"} and (not row["limited"] or row["slots"] > 1))):
            raise ValueError("invalid or stale host capacity")
        return row
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"state": "UNKNOWN", "limited": True, "slots": 1, "cooldown": 30,
                "render_timeout": 30, "reason": str(exc)}


def observe(slice_path):
    try:
        path = Path(slice_path)
        current, high, maximum = [int((path / name).read_text())
                                  for name in ("memory.current", "memory.high", "memory.max")]
        events = dict((key, int(value)) for key, value in
                      (line.split() for line in (path / "memory.events").read_text().splitlines()))
        return dict(recovery.pressure(), current=current, high=high, max=maximum, events=events)
    except (OSError, ValueError, TypeError):
        return None


def unit_states(names):
    if not names:
        return {}
    receipt = recovery.run_probe(["systemctl", "--user", "show", "--property=Id,ActiveState", *names], timeout=3)
    if receipt["ok"] is not True:
        raise ValueError("optional service states unavailable: " + receipt["stderr"])
    states = {}
    for block in receipt["stdout"].split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if fields.get("Id") in names:
            states[fields["Id"]] = fields.get("ActiveState", "unknown")
    if (set(states) != set(names) or
            any(value not in {"active", "inactive", "failed", "activating", "deactivating", "reloading"}
                for value in states.values())):
        raise ValueError("incomplete optional service states")
    return states


def unit_action(verb, units):
    return recovery.run_probe(["systemctl", "--user", verb, "--no-block", *units], timeout=3)


def reconcile(root, row, config):
    names = config["optional_units"]
    if (not isinstance(names, list) or len(names) > 16 or
            any(not isinstance(n, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.service", n) for n in names)):
        raise ValueError("invalid optional service inventory")
    managed = set(row.get("managed_units", []))
    pending = dict(row.get("pending_stops", {}))
    starts = dict(row.get("pending_starts", {}))
    if not (managed | set(pending) | set(starts)) <= set(names):
        raise ValueError("managed optional inventory removed; reconcile before changing coverage")
    states = unit_states(names)
    for name, action in list(pending.items()):
        if action["phase"] == "requested" and states[name] in {"inactive", "failed"}:
            managed.add(name)
            del pending[name]
        elif action["phase"] in {"intent", "UNKNOWN"}:
            if states[name] == "active":
                # An idempotent stop can be tried again while still active;
                # an interrupted stop never authorizes a blind later start.
                del pending[name]
            else:
                row["action_error"] = "uncertain interrupted stop: " + name
        elif row["checked_at"] - action["at"] > 30:
            row["action_error"] = "stop not observed within 30s: " + name
    if not row["limited"]:
        managed.difference_update(n for n in managed if states[n] == "active")
        for name, stamp in list(starts.items()):
            if states[name] == "active":
                del starts[name]
            elif row["checked_at"] - stamp > 30:
                row["action_error"] = "restoration not observed within 30s: " + name
    else:
        starts.clear()
    verb = "stop" if row["limited"] else "start"
    targets = ([n for n in names if states[n] in {"active", "activating"} and n not in pending]
               if row["limited"] else [n for n in sorted(managed) if states[n] in {"inactive", "failed"}])
    row.update(managed_units=sorted(managed), pending_stops=pending, pending_starts=starts)
    if targets:
        intent = {"at": row["checked_at"], "verb": verb, "units": targets, "phase": "intent"}
        row["action"] = intent
        if verb == "stop":
            pending.update({n: {"at": row["checked_at"], "phase": "intent"} for n in targets})
        else:
            for name in targets:
                starts.setdefault(name, row["checked_at"])
        recovery.save(Path(root) / "host-capacity.json", row)
        recovery.event(Path(root), {"host_action": intent})
        receipt = unit_action(verb, targets)
        row["action"] = dict(intent, phase="requested" if receipt["ok"] else "UNKNOWN", receipt=receipt)
        recovery.event(Path(root), {"host_action": row["action"]})
        after = unit_states(names)
        if verb == "stop":
            for name in targets:
                if receipt["ok"] is True:
                    if after[name] in {"inactive", "failed"}:
                        managed.add(name)
                        pending.pop(name, None)
                    else:
                        pending[name]["phase"] = "requested"
                elif after[name] == "active" and receipt["ok"] is False:
                    pending.pop(name, None)
                else:
                    pending[name]["phase"] = "UNKNOWN"
        else:
            # A queued job is not evidence that a service actually started.
            managed.difference_update(n for n in targets if after[n] == "active")
            for name in targets:
                if after[name] == "active":
                    starts.pop(name, None)
        if receipt["ok"] is not True:
            row["action_error"] = "optional " + verb + " failed or uncertain: " + receipt.get("stderr", "")
        row.update(managed_units=sorted(managed), pending_stops=pending, pending_starts=starts)
    return row


def tick(root, config, *, now=None):
    root = Path(root)
    realtime = now is None
    now = time.time() if now is None else now
    root.mkdir(parents=True, exist_ok=True)
    try:
        with (root / "host-capacity.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Another tick holds the lock; busy is not a failure.
                # Return the last observed row if fresh, otherwise UNKNOWN
                # without crashing the systemd unit into a restart loop.
                path = root / "host-capacity.json"
                if path.exists():
                    try:
                        row = recovery.read_json(path)
                        if isinstance(row, dict) and row.get("version") == 1 and 0 <= now - row.get("checked_at", now) <= row.get("max_age", 20):
                            return row
                    except (ValueError, OSError):
                        pass
                return {"version": 1, "checked_at": now, "max_age": 20, "state": "UNKNOWN", "limited": True, "slots": 1, "cooldown": 30, "render_timeout": 30, "reason": "capacity controller busy; retry"}
            path = root / "host-capacity.json"
            previous = recovery.read_json(path) if path.exists() else None
            if previous is not None and (not isinstance(previous, dict) or previous.get("version") != 1):
                raise ValueError("corrupt capacity state retained; reconcile before reset")
            row = decide(previous, observe(config["slice_path"]), now=now,
                         stable_seconds=config.get("stable_seconds", 120))
            try:
                row = reconcile(root, row, config)
            except (OSError, ValueError) as exc:
                row["action_error"] = str(exc)
            try:
                guard_row = recovery.read_json(root / "host-capacity-guard.json")
                # A guard may update after acquisition began but before this read.
                # Compare with the read time, not the older sample-start timestamp.
                observed_at = time.time() if realtime else now
                guard_fresh = (isinstance(guard_row, dict)
                               and guard_row.get("state") in {"FRESH", "UNKNOWN"}
                               and guard_row.get("capacity") in {"NORMAL", "LIMITED", "CRITICAL", "UNKNOWN"}
                               and guard_row.get("scope") == "capacity producer freshness; not restoration"
                               and type(guard_row.get("checked_at")) in (int, float)
                               and 0 <= observed_at - guard_row["checked_at"] <= 20)
            except (ValueError, TypeError, KeyError):
                guard_fresh = False
            row["guard_fresh"] = guard_fresh
            if not guard_fresh:
                row["action_error"] = row.get("action_error", "independent host guard missing or stale")
            if row.get("action_error"):
                row.update(resource_state=row["state"], state="UNKNOWN", limited=True,
                           slots=min(row["slots"], 1), cooldown=30)
            recovery.save(path, row)
            with (root / "host-samples.jsonl").open("a") as log:
                import json
                log.write(json.dumps(row, allow_nan=False) + "\n")
            if (not previous or previous["state"] != row["state"] or row["oom_delta"]
                    or previous.get("action_error") != row.get("action_error")):
                recovery.event(root, {"host_capacity": row, "resolver": "health", "backup": "genome",
                                      "deadline_seconds": 300}, recipients=["health", "genome"])
            return row
    except BlockingIOError:
        # Fallback if lock file itself contended at open; treat as busy not failure
        return {"version": 1, "checked_at": now, "max_age": 20, "state": "UNKNOWN", "limited": True, "slots": 1, "cooldown": 30, "render_timeout": 30, "reason": "capacity controller busy; retry"}


def guard(root, *, now=None):
    now = time.time() if now is None else now
    row = status(root, now=now)
    result = {"checked_at": now, "state": "UNKNOWN" if "reason" in row else "FRESH",
              "capacity": row["state"], "scope": "capacity producer freshness; not restoration"}
    path = Path(root) / "host-capacity-guard.json"
    try:
        previous = recovery.read_json(path)
    except ValueError:
        previous = {}
    if previous.get("state") != result["state"]:
        recovery.event(Path(root), {"host_guard": result}, recipients=["health", "genome"])
    recovery.save(path, result)
    return result


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("action", choices=("watch", "once", "guard", "status"))
    args = parser.parse_args()
    if args.action in {"guard", "status"}:
        row = guard(args.root) if args.action == "guard" else status(args.root)
        print(json.dumps(row))
        return 3 if row["state"] == "UNKNOWN" else 0
    config = recovery.read_json(args.root / "host-config.json")
    while True:
        row = tick(args.root, config)
        recovery.notify_watchdog()
        if args.action == "once":
            print(json.dumps(row))
            return 0
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
