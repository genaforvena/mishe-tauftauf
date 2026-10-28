"""Standalone Linux/systemd boundary adapter, installed root-owned outside plants.

No plant imports, feed reads, shared locks, provider calls or chat dependencies.
The kernel limits memory/CPU/tasks; this sentinel latches unsafe operation off.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import hashlib
import math
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time

GIB = 1024 ** 3
STATE = Path("/var/lib/mishe-containment")
RUN = Path("/run/mishe-containment")
CONFIG = Path("/etc/mishe-containment.json")
MAX_AGE = 20
STABLE_SECONDS = 120


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
        if "stop_cgroups" in config and (not isinstance(config["stop_cgroups"], list)
                or not config["stop_cgroups"] or any(not isinstance(path, str) for path in config["stop_cgroups"])):
            return False
        life = config.get("life_support")
        if getattr(args, "require_life_support", False) and life is None:
            return False
        if life is not None and (not isinstance(life, dict)
                or not all(isinstance(life.get(key), str) for key in ("cgroup", "socket", "session", "home"))
                or not isinstance(life.get("units"), list)
                or not life["units"] or any(not isinstance(unit, str) for unit in life["units"])
                or any(unit in config["units"] for unit in life["units"])
                or not isinstance(config.get("stop_cgroups"), list)):
            return False
        if life is not None and (set(config["stop_cgroups"]) != {
                config["cgroup"] + "/mishe-plants.slice", config["cgroup"] + "/mishe-repair.slice"}
                or life["cgroup"] != config["cgroup"] + "/mishe-lifeline.slice"
                or not isinstance(life.get("roles"), list)
                or not life["roles"] or any(not isinstance(role, str) for role in life["roles"])
                or not isinstance(config.get("resume_units"), list)
                or any(not isinstance(unit, str) for unit in config["resume_units"])):
            return False
        return all(isinstance(path, str) and isinstance(limits, dict) and limits
                   and all(isinstance(name, str) and type(value) in (str, int) for name, value in limits.items())
                   for path, limits in config["limits"].items())
    except (KeyError, TypeError, ValueError):
        return False


def sentinel_healthy(state=None, run=None, *, now=None):
    state = STATE if state is None else state
    run = RUN if run is None else run
    now = time.time() if now is None else now
    try:
        row = read(run / "status.json")
        return (not (state / "tripped").exists() and not (state / "emergency").exists()
                and row["state"] == "READY"
                and 0 <= now - row["at"] <= MAX_AGE)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def admitted(state=None, run=None, *, now=None, unit=None):
    """While restoring, only the unit the restoration is starting is admitted."""
    state = STATE if state is None else state
    now = time.time() if now is None else now
    if not sentinel_healthy(state, run, now=now):
        return False
    if not (state / "restoring").exists():
        return True
    try:
        row = read(state / "restoring")
        return unit is not None and row["next_unit"] == unit and 0 <= now - row["at"] <= 20
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
    receipts = stop(config)
    try:
        with (STATE / "emergency").open("x") as stream:
            json.dump(dict(at=time.time(), reason=why), stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    except OSError as exc:
        print("EMERGENCY latch persistence failed: " + str(exc), file=sys.stderr, flush=True)
    return receipts


def pressure(name):
    rows = Path("/proc/pressure", name).read_text().splitlines()
    row = next(row for row in rows if row.startswith("full "))
    return float(dict(part.split("=", 1) for part in row.split()[1:])["avg10"])


def bounded_command(command, *, timeout=1, receipt=None):
    """Bound fork/exec and all output drain; a timed-out probe owns its group."""
    receipt = {} if receipt is None else receipt
    started = time.monotonic()
    receipt.update(timeout_seconds=timeout, timed_out=False)
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError as exc:
        receipt.update(error=str(exc), seconds=time.monotonic() - started)
        return None
    try:
        output, error = proc.communicate(timeout=timeout)
        receipt.update(code=proc.returncode, stderr=error[-2048:].decode("utf-8", "replace"),
                       seconds=time.monotonic() - started)
        return proc.returncode, output
    except subprocess.TimeoutExpired as exc:
        receipt["timed_out"] = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=.2)
        except subprocess.TimeoutExpired:
            proc.stdout.close()
            proc.stderr.close()
        receipt.update(seconds=time.monotonic() - started,
                       stderr=(exc.stderr or b"")[-2048:].decode("utf-8", "replace"))
        return None


def filesystem_probe(directory="/var/tmp"):
    payload = b"mishe host filesystem probe\n"
    try:
        with tempfile.TemporaryFile(dir=directory) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            return stream.read() == payload
    except OSError as exc:
        print("UNKNOWN host filesystem probe: " + str(exc), file=sys.stderr, flush=True)
        return False


def functional_probes(config):
    # Key exchange reaches beyond the banner. The separate user process checks
    # fork/exec, root-filesystem writes/fsync/read and user identity separately.
    receipts = dict(kex={}, io={})
    kex = bounded_command(["/usr/bin/ssh-keyscan", "-T", "4", "-t", "ed25519", "127.0.0.1"], timeout=5, receipt=receipts["kex"])
    io = bounded_command(["/usr/sbin/runuser", "-u", config["user"], "--", "/usr/bin/python3",
                          str(Path(__file__).resolve()), "io-probe"], timeout=5, receipt=receipts["io"])
    return bool(kex and kex[0] == 0 and b" ssh-ed25519 " in kex[1]), bool(io and io[0] == 0), receipts


def record_pressure(state, row, warning):
    atomic(state / ("pressure-" + str(time.time_ns()) + ".json"),
           dict(at=time.time(), warning=warning, observation=row, resolver="host-sentinel",
                next_probe_seconds=2,
                action="repeat production SSH key exchange and user fork/exec/fsync/read; stop on any failure",
                disposition="bounded functional probes recorded; pressure cause remains open"))


def observe(config, *, functional=True):
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
        if functional:
            row["ssh_available"], row["host_io_available"], row["probes"] = functional_probes(config)
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
    if row.get("ssh_available") is not True:
        return "SSH listener unavailable", None
    if row.get("host_io_available") is not True:
        return "host filesystem probe unavailable", None
    if row["oom_kill"] > (previous or row)["oom_kill"]:
        return "cgroup OOM", None
    if row["disk_free"] < 10 * GIB:
        return "root disk reserve low", None
    # Global PSI includes tasks deliberately throttled by our cgroup limits.
    # Preserve those observations; use actual host headroom/listener failure
    # to distinguish them from a threatened host. Never relax kernel ceilings.
    pressured = (row["available"] < 4 * GIB or row["memory"] >= row["maximum"] * .95
                 or row["tasks"] >= row["task_max"] * .95)
    since = (now if pressured_since is None else pressured_since) if pressured else None
    if since is not None and now - since >= 10:
        return "sustained resource pressure", since
    return None, since


def clear_to_rearm(row):
    return bool(row and row["limits_ok"] and row.get("ssh_available") is True
                and row.get("host_io_available") is True
                and row["memory"] < row["maximum"] * .7
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
    paths = config.get("stop_cgroups", [config.get("cgroup")])
    if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths):
        base = str(config.get("cgroup", ""))
        paths = [base + "/mishe-plants.slice", base + "/mishe-repair.slice"]
    for target in paths:
        try:
            path = Path(target) / "cgroup.kill"
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


def lifeline_safe(config):
    try:
        path = Path(config["life_support"]["cgroup"])
        limits = config["limits"][str(path)]
        if not all((path / name).read_text().strip() == str(value) for name, value in limits.items()):
            return False
        memory, tasks = [int((path / name).read_text()) for name in ("memory.max", "pids.max")]
        quota, period = [int(value) for value in (path / "cpu.max").read_text().split()]
        return 0 < memory <= 3 * GIB and 0 < tasks <= 256 and 0 < quota <= period
    except (OSError, KeyError, TypeError, ValueError):
        return False


def life_observation(config):
    life = config["life_support"]
    result = bounded_command(["/usr/sbin/runuser", "-u", config["user"], "--", "/usr/bin/tmux", "-S", life["socket"], "list-panes", "-s", "-t", life["session"],
                              "-F", "#{window_name}\t#{pane_index}\t#{pane_dead}\t#{pane_pid}\t#{@mishe-tauftauf-home}"], timeout=2)
    if not result or result[0] != 0:
        return dict(state="UNKNOWN", reason="protected session observation unavailable")
    found = {}
    try:
        for raw in result[1].decode("utf-8").splitlines():
            name, index, dead, pid, owner = raw.split("\t")
            if index in {"0", "1"} and name in life["roles"]:
                cgroup = Path("/proc", str(int(pid)), "cgroup").read_text()
                expected = "0::" + life["cgroup"].removeprefix("/sys/fs/cgroup")
                contained = any(line == expected or line.startswith(expected + "/") for line in cgroup.splitlines())
                found[name + "." + index] = dict(pid=int(pid), live=dead == "0" and owner == life["home"] and contained)
        live = all(found.get(role + "." + index, {}).get("live") for role in life["roles"] for index in ("0", "1"))
        return dict(state="PANES_LIVE" if live else "RED", panes=found,
                    activity="pane liveness only; useful work requires tool/wall evidence")
    except (OSError, UnicodeError, ValueError):
        return dict(state="UNKNOWN", reason="malformed protected session observation", raw=result[1][-2048:].decode("utf-8", "replace"))


def ensure_life_support(config, *, start=False):
    if "life_support" not in config:
        return None
    if not lifeline_safe(config):
        return dict(state="RED", reason="life-support kernel budget missing or changed")
    row = life_observation(config)
    ui = bounded_command(["/usr/sbin/runuser", "-u", config["user"], "--", "/usr/bin/tmux", "-S", config["life_support"]["socket"],
                          "set-option", "-g", "prefix", "C-a", ";", "bind-key", "C-a", "send-prefix", ";",
                          "set-option", "-g", "status-right", "#(/usr/bin/python3 /usr/local/lib/mishe-containment/guard.py mode)"], timeout=2)
    row["ui_configured"] = bool(ui and ui[0] == 0)
    if start and row["state"] != "PANES_LIVE":
        try:
            result = user_command(config, "start", "--no-block", *config["life_support"]["units"])
            row["start_request"] = dict(code=result.returncode, stderr=result.stderr[-2048:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            row["start_request"] = dict(error=str(exc))
    return row


def restoration_expired(now=None):
    if not (STATE / "restoring").exists():
        return False
    try:
        row = read(STATE / "restoring")
        return not 0 <= (time.time() if now is None else now) - row["at"] <= 20
    except (OSError, ValueError, KeyError, TypeError):
        return True


def rearm(config, *, restoration=False, request=None):
    with transition(STATE, timeout=.5):
        status = read(RUN / "status.json")
        if status["state"] not in {"READY", "TRIPPED"} or not 0 <= time.time() - status["at"] <= MAX_AGE:
            raise SystemExit("sentinel is stale or unknown; rearm refused")
        if (STATE / "emergency").exists() and not restoration:
            raise SystemExit("emergency latch requires explicit host investigation; rearm refused")
        # Reuse the sentinel's fresh functional result; do not serialize a
        # second long probe interval behind the running sentinel's lock.
        row = observe(config, functional=False)
        if row:
            for key in ("ssh_available", "host_io_available", "probes"):
                row[key] = status.get("observation", {}).get(key)
        if not clear_to_rearm(row):
            raise SystemExit("resource headroom or functional checks not clear; rearm refused")
        if restoration:
            if status.get("life_support", {}).get("state") != "PANES_LIVE":
                raise SystemExit("protected recovery session not live; restore refused")
            since = status.get("clear_since")
            if not isinstance(since, (int, float)) or time.monotonic() - since < STABLE_SECONDS:
                raise SystemExit("continuous healthy stability interval incomplete; restore refused")
            if not isinstance(request, dict):
                raise SystemExit("restoration intent missing")
            # A killed caller must always leave either a closed latch or an
            # observable restoration heartbeat. Publish it before admission.
            atomic(STATE / "restoring", request)
        for name in ("tripped", "emergency") if restoration else ("tripped",):
            if (STATE / name).exists():
                os.replace(STATE / name, STATE / (name + "-" + str(time.time_ns()) + ".json"))


def rearm_when_lock_free(config, request, attempts=10):
    """Transition-lock contention during a slow probe is retried, not treated as a refusal."""
    for attempt in range(attempts):
        try:
            return rearm(config, restoration=True, request=request)
        except TimeoutError:
            if attempt == attempts - 1:
                raise
            time.sleep(.5)


def restore(config, evidence):
    if evidence is None:
        raise SystemExit("restore requires a retained diagnosis/verification artifact")
    path = Path(evidence).resolve()
    if not path.is_relative_to(Path(config["evidence_root"]).resolve()) or not path.is_file():
        raise SystemExit("restore evidence must be inside the configured artifact directory")
    request = dict(id=str(time.time_ns()), evidence=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), at=time.time())
    with (STATE / "restore.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another restoration is in progress")
        atomic(STATE / ("restore-" + request["id"] + "-request.json"), request)
        # Refusals before restoration starts leave the existing latch untouched.
        rearm_when_lock_free(config, request)
        try:
            deadline = time.monotonic() + MAX_AGE + 3
            while read(RUN / "status.json")["state"] != "READY":
                if (STATE / "tripped").exists() or (STATE / "emergency").exists() or time.monotonic() > deadline:
                    raise RuntimeError("host did not enter healthy restoring mode")
                time.sleep(.2)
            for index, unit in enumerate(config["resume_units"]):
                if unit in config["life_support"]["units"]:
                    continue
                if not sentinel_healthy():
                    raise RuntimeError("host safety boundary closed during staged restoration")
                atomic(STATE / "restoring", dict(request, at=time.time(), next_unit=unit))
                atomic(STATE / ("restore-" + request["id"] + "-" + str(index) + "-intent.json"), dict(unit=unit, at=time.time()))
                result = user_command(config, "start", "--no-block", unit)
                atomic(STATE / ("restore-" + request["id"] + "-" + str(index) + "-result.json"),
                       dict(unit=unit, at=time.time(), code=result.returncode, stderr=result.stderr[-2048:]))
                if result.returncode:
                    raise RuntimeError("unit start request failed: " + unit)
                time.sleep(1)
            if not sentinel_healthy():
                raise RuntimeError("host safety boundary closed at restoration completion")
            (STATE / "restoring").unlink()
            atomic(STATE / ("restore-" + request["id"] + "-complete.json"), dict(request, completed_at=time.time(),
                   scope="start requests accepted for every admitted unit; per-unit activation not verified by this controller; not causal incident closure"))
        except BaseException:
            stop(config)
            latch(STATE, "staged restoration failed or interrupted", None)
            raise


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)


def watch(config):
    stop(config)
    STATE.mkdir(parents=True, exist_ok=True)
    RUN.mkdir(parents=True, exist_ok=True)
    # Any supervisor interruption requires explicit rearm. A restart cannot
    # erase a pressure interval or turn an observation gap into recovery.
    with transition(STATE):
        latch(STATE, "sentinel started/restarted: explicit rearm required", None)
    previous = None
    since = None
    old_warning = False
    last_pressure_record = 0
    clear_since = None
    last_tick = time.monotonic()
    last_life_start = -100
    notify("READY=1")
    while True:
        with transition(STATE):
            row = observe(config)
            why, since = reason(row, previous, since, time.monotonic())
            if restoration_expired():
                why = "staged restoration heartbeat missing, malformed or stale"
                os.replace(STATE / "restoring", STATE / ("restore-abandoned-" + str(time.time_ns()) + ".json"))
            stopped = None
            if why:
                stopped = stop(config)
                latch(STATE, why, row)
            tripped = (STATE / "tripped").exists() or (STATE / "emergency").exists()
            now = time.monotonic()
            if why is None and clear_to_rearm(row):
                clear_since = clear_since if clear_since is not None and 0 <= now - last_tick <= MAX_AGE else now
            else:
                clear_since = None
            last_tick = now
            status = dict(at=time.time(), state="TRIPPED" if tripped else "READY",
                          mode="LIFE_SUPPORT" if tripped else "RESTORING" if (STATE / "restoring").exists() else "NORMAL",
                          observation=row, pressured_since=since, clear_since=clear_since,
                          pressure_warning=bool(row and (row["memory_pressure"] >= 10 or row["io_pressure"] >= 25
                              or row.get("probes", {}).get("io", {}).get("seconds", 0) >= 1)))
            warning = status["pressure_warning"]
            if warning != old_warning or (warning and time.monotonic() - last_pressure_record >= 60):
                record_pressure(STATE, row, warning)
                last_pressure_record = time.monotonic()
            old_warning = warning
            if tripped and stopped is None:
                stopped = stop(config)
            if stopped is not None:
                status["stop"] = stopped
            status["life_support"] = ensure_life_support(config, start=now - last_life_start >= 30)
            if status["life_support"] and "start_request" in status["life_support"]:
                last_life_start = now
            if status["life_support"] and status["life_support"]["state"] != "PANES_LIVE":
                if stopped is None:
                    status["stop"] = stop(config)
                latch(STATE, "protected recovery session missing, dead or unobservable", row)
                status.update(state="TRIPPED", mode="LIFE_SUPPORT", clear_since=None)
                clear_since = None
            atomic(RUN / "status.json", status)
        previous = row
        notify("WATCHDOG=1")
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgroup", help="root unit's independent emergency target")
    parser.add_argument("--user", default="mesh-home")
    parser.add_argument("--uid", type=int, default=1000)
    parser.add_argument("--require-life-support", action="store_true")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("action", choices=("io-probe", "prepare", "watch", "check", "trip", "rearm", "restore", "admit", "admit-life", "status", "mode"))
    parser.add_argument("unit", nargs="?", default="")
    args = parser.parse_args()
    if args.action == "io-probe":
        return 0 if filesystem_probe() else 1
    if args.action == "admit":
        return 0 if admitted(unit=args.unit or None) else 1
    if args.action in {"status", "mode"}:
        try:
            status = read(RUN / "status.json")
            if not 0 <= time.time() - status["at"] <= MAX_AGE:
                raise ValueError("sentinel evidence stale or future")
            result = dict(status=status,
                          trip=read(STATE / "tripped") if (STATE / "tripped").exists() else None,
                          emergency=read(STATE / "emergency") if (STATE / "emergency").exists() else None)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print("UNKNOWN" if args.action == "mode" else json.dumps(dict(state="UNKNOWN", reason=str(exc))))
            return 1
        print(result["status"].get("mode", "UNKNOWN") if args.action == "mode" else json.dumps(result))
        return 0
    try:
        config = read(CONFIG)
        if not valid_config(config, args):
            raise ValueError("incomplete containment configuration")
    except (OSError, ValueError, TypeError) as exc:
        print("UNKNOWN containment configuration: " + str(exc), file=sys.stderr, flush=True)
        if args.cgroup and args.action != "admit-life":
            emergency(dict(cgroup=args.cgroup, user=args.user, uid=args.uid, units=[],
                           stop_cgroups=[args.cgroup + "/mishe-plants.slice", args.cgroup + "/mishe-repair.slice"]), "configuration unavailable")
        return 1
    if args.action == "admit-life":
        return 0 if args.unit in config.get("life_support", {}).get("units", []) and lifeline_safe(config) else 1
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
            healthy = status["state"] in {"READY", "TRIPPED"} and 0 <= time.time() - status["at"] <= MAX_AGE
        except (OSError, ValueError, KeyError, TypeError):
            healthy = False
        if args.action == "trip" or not healthy:
            why = "external sentinel missing/stale/failed" if args.action == "check" else "explicit stop"
            receipts = stop(config)
            try:
                with transition(STATE, timeout=.1):
                    latch(STATE, why, None)
                    print(json.dumps(receipts))
            except (TimeoutError, OSError):
                print(json.dumps(emergency(config, why + "; transition lock wedged")))
            return 1
    elif args.action == "rearm":
        if "life_support" in config:
            raise SystemExit("use restore --evidence with life support enabled")
        rearm(config)
        print("Rearmed. Start residents explicitly; interrupted wakes remain unreconciled.")
    elif args.action == "restore":
        restore(config, args.evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
