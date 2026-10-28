"""Standalone Linux/systemd boundary adapter, installed root-owned outside plants.

No plant imports, feed reads, shared locks, provider calls or chat dependencies.
The kernel limits memory/CPU/tasks; this sentinel latches unsafe operation off.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

GIB = 1024 ** 3
STATE = Path("/var/lib/mishe-containment")
RUN = Path("/run/mishe-containment")
CONFIG = Path("/etc/mishe-containment.json")


@contextmanager
def transition(state, timeout=None):
    """Serialize root trip/rearm transitions, including their observations."""
    with (state / "transition.lock").open("a") as stream:
        if timeout is None:
            fcntl.flock(stream, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("containment transition lock unavailable")
                    time.sleep(.01)
        yield


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".new")
    with temp.open("w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def read(path):
    return json.loads(Path(path).read_text())


def valid_config(config, args):
    try:
        if (not isinstance(config, dict) or type(config["uid"]) is not int or config["uid"] < 1
                or not isinstance(config["user"], str) or not config["user"]
                or not isinstance(config["cgroup"], str) or not Path(config["cgroup"]).is_absolute()
                or not isinstance(config["units"], list) or not isinstance(config["limits"], dict)
                or not config["limits"]):
            return False
        if args.cgroup and (config["cgroup"], config["user"], config["uid"]) != (args.cgroup, args.user, args.uid):
            return False
        if any(not isinstance(unit, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@:-]*\.(service|timer)", unit)
               for unit in config["units"]):
            return False
        return all(isinstance(path, str) and isinstance(limits, dict) and limits
                   and all(isinstance(name, str) and type(value) in (str, int) for name, value in limits.items())
                   for path, limits in config["limits"].items())
    except (KeyError, TypeError, ValueError):
        return False


def admitted(state=STATE, run=RUN, *, now=None):
    now = time.time() if now is None else now
    try:
        row = read(run / "status.json")
        return (not (state / "tripped").exists() and not (state / "emergency").exists()
                and row["state"] == "READY"
                and 0 <= now - row["at"] <= 6)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def latch(state, why, observation):
    """O_EXCL preserves the original failure, including across sentinel restarts."""
    path = state / "tripped"
    try:
        with path.open("x") as stream:
            json.dump(dict(at=time.time(), reason=why, observation=observation), stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass


def emergency(config, why):
    """Independent sticky marker: ordinary rearm can never remove this latch."""
    try:
        with (STATE / "emergency").open("x") as stream:
            json.dump(dict(at=time.time(), reason=why), stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    except OSError as exc:
        print("EMERGENCY latch persistence failed: " + str(exc), file=sys.stderr, flush=True)
    return stop(config)


def pressure(name):
    rows = Path("/proc/pressure", name).read_text().splitlines()
    row = next(row for row in rows if row.startswith("full "))
    return float(dict(part.split("=", 1) for part in row.split()[1:])["avg10"])


def observe(config):
    try:
        base = Path(config["cgroup"])
        events = dict(line.split() for line in (base / "memory.events").read_text().splitlines())
        meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        limits_ok = all((Path(path) / name).read_text().strip() == str(value)
                        for path, limits in config["limits"].items()
                        for name, value in limits.items())
        row = dict(oom_kill=int(events["oom_kill"]),
                   memory=int((base / "memory.current").read_text()),
                   maximum=int((base / "memory.max").read_text()),
                   tasks=int((base / "pids.current").read_text()),
                   task_max=int((base / "pids.max").read_text()),
                   available=int(meminfo["MemAvailable"].split()[0]) * 1024,
                   disk_free=shutil.disk_usage("/").free,
                   memory_pressure=pressure("memory"), io_pressure=pressure("io"),
                   limits_ok=limits_ok)
        if (row["maximum"] <= 0 or row["task_max"] <= 0
                or any(not math.isfinite(row[key]) or not 0 <= row[key] <= 100
                       for key in ("memory_pressure", "io_pressure"))):
            return None
        return row
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None


def prepare(config):
    """The already-running user manager may not enable newly delegated I/O."""
    base = Path(config["cgroup"])
    (base.parent / "cgroup.subtree_control").write_text("+io")
    (base / "io.max").write_text(config["limits"][str(base)]["io.max"])


def reason(row, previous, pressured_since, now):
    if row is None:
        return "resource observation unavailable", None
    if not row["limits_ok"]:
        return "resource limits changed", None
    if row["oom_kill"] > (previous or row)["oom_kill"]:
        return "cgroup OOM", None
    if row["disk_free"] < 10 * GIB:
        return "root disk reserve low", None
    pressured = (row["available"] < 4 * GIB or row["memory_pressure"] >= 10
                 or row["io_pressure"] >= 25 or row["memory"] >= row["maximum"] * .95
                 or row["tasks"] >= row["task_max"] * .95)
    since = (now if pressured_since is None else pressured_since) if pressured else None
    if since is not None and now - since >= 10:
        return "sustained resource pressure", since
    return None, since


def clear_to_rearm(row):
    return bool(row and row["limits_ok"] and row["memory"] < row["maximum"] * .7
                and row["tasks"] < row["task_max"] * .7 and row["available"] >= 6 * GIB
                and row["disk_free"] >= 12 * GIB and row["memory_pressure"] < 1
                and row["io_pressure"] < 10)


def user_command(config, *args):
    return subprocess.run(["/usr/sbin/runuser", "-u", config["user"], "--", "/usr/bin/env",
                           "XDG_RUNTIME_DIR=/run/user/" + str(config["uid"]),
                           "/usr/bin/systemctl", "--user", *args],
                          capture_output=True, text=True, timeout=1.5)


def stop(config):
    """Timers are stopped too; cgroup.kill catches orphaned transient scopes."""
    receipts = []
    # The kernel stop is independent of user-manager and inventory failures.
    try:
        path = Path(config["cgroup"]) / "cgroup.kill"
        if path.exists():
            path.write_text("1")
        receipts.append(dict(kill="requested"))
    except (OSError, KeyError, TypeError) as exc:
        receipts.append(dict(kill_error=str(exc)))
    try:
        units = config.get("units", [])
        if not isinstance(units, list):
            raise ValueError("malformed stop inventory")
        args = ("stop", "--no-block", *units)
        try:
            result = user_command(config, *args)
            receipts.append(dict(code=result.returncode, stderr=result.stderr[-2000:]))
        except (OSError, subprocess.TimeoutExpired, KeyError, TypeError) as exc:
            receipts.append(dict(error=str(exc)))
    except (ValueError, TypeError) as exc:
        receipts.append(dict(error=str(exc)))
    return receipts


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)


def watch(config):
    STATE.mkdir(parents=True, exist_ok=True)
    RUN.mkdir(parents=True, exist_ok=True)
    # Any supervisor interruption requires explicit rearm. A restart cannot
    # erase a pressure interval or turn an observation gap into recovery.
    with transition(STATE):
        try:
            latch(STATE, "sentinel started/restarted: explicit rearm required", None)
        except OSError:
            stop(config)
            raise
    previous = None
    since = None
    notify("READY=1")
    while True:
        with transition(STATE):
            row = observe(config)
            why, since = reason(row, previous, since, time.monotonic())
            if why:
                try:
                    latch(STATE, why, row)
                except OSError:
                    stop(config)
                    raise
            tripped = (STATE / "tripped").exists() or (STATE / "emergency").exists()
            status = dict(at=time.time(), state="TRIPPED" if tripped else "READY",
                          observation=row, pressured_since=since)
            if tripped:
                status["stop"] = stop(config)
            atomic(RUN / "status.json", status)
        previous = row
        notify("WATCHDOG=1")
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgroup", help="root unit's independent emergency target")
    parser.add_argument("--user", default="mesh-home")
    parser.add_argument("--uid", type=int, default=1000)
    parser.add_argument("action", choices=("prepare", "watch", "check", "trip", "rearm", "admit", "status"))
    parser.add_argument("unit", nargs="?", default="")
    args = parser.parse_args()
    if args.action == "admit":
        return 0 if admitted() else 1
    if args.action == "status":
        try:
            status = read(RUN / "status.json")
            if not 0 <= time.time() - status["at"] <= 6:
                raise ValueError("sentinel evidence stale or future")
            result = dict(status=status,
                          trip=read(STATE / "tripped") if (STATE / "tripped").exists() else None,
                          emergency=read(STATE / "emergency") if (STATE / "emergency").exists() else None)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(json.dumps(dict(state="UNKNOWN", reason=str(exc))))
            return 1
        print(json.dumps(result))
        return 0
    try:
        config = read(CONFIG)
        if not valid_config(config, args):
            raise ValueError("incomplete containment configuration")
    except (OSError, ValueError, TypeError) as exc:
        print("UNKNOWN containment configuration: " + str(exc), file=sys.stderr, flush=True)
        if args.cgroup:
            emergency(dict(cgroup=args.cgroup, user=args.user, uid=args.uid, units=[]), "configuration unavailable")
        return 1
    if args.action == "prepare":
        prepare(config)
    elif args.action == "watch":
        watch(config)
    elif args.action in {"check", "trip"}:
        try:
            STATE.mkdir(parents=True, exist_ok=True)
        except OSError:
            print(json.dumps(emergency(config, "state directory unavailable")))
            return 1
        try:
            status = read(RUN / "status.json")
            healthy = status["state"] in {"READY", "TRIPPED"} and 0 <= time.time() - status["at"] <= 6
        except (OSError, ValueError, KeyError, TypeError):
            healthy = False
        if args.action == "trip" or not healthy:
            why = "external sentinel missing/stale/failed" if args.action == "check" else "explicit stop"
            try:
                with transition(STATE, timeout=.1):
                    try:
                        latch(STATE, why, None)
                    finally:
                        print(json.dumps(stop(config)))
            except (TimeoutError, OSError):
                print(json.dumps(emergency(config, why + "; transition lock wedged")))
            return 1
    elif args.action == "rearm":
        with transition(STATE):
            if not clear_to_rearm(observe(config)):
                raise SystemExit("resource headroom is not clear; rearm refused")
            status = read(RUN / "status.json")
            if status["state"] not in {"READY", "TRIPPED"} or not 0 <= time.time() - status["at"] <= 6:
                raise SystemExit("sentinel is stale or unknown; rearm refused")
            if (STATE / "emergency").exists():
                raise SystemExit("emergency latch requires explicit host investigation; rearm refused")
            if (STATE / "tripped").exists():
                archive = STATE / ("trip-" + str(time.time_ns()) + ".json")
                os.replace(STATE / "tripped", archive)
        print("Rearmed. Start residents explicitly; interrupted wakes remain unreconciled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
