"""Literal recovery boundary: bounded local files, Linux probes and commands.

The control loop never reads the chat tape or takes its locks. Notification
publication is a separate, timed process with an immutable local outbox.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import selectors
import socket
import subprocess
import sys
import tempfile
import time

from . import recovery_policy as policy

MAX_BYTES = 4 * 1024 * 1024
STATES = {"HEALTHY", "FAILED", "MITIGATED", "RECOVERING", "RESTORED", "UNKNOWN"}


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"missing, nonregular or oversized recovery input: {path}")
    try:
        return json.loads(path.read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, ValueError) as exc:
        raise ValueError(f"unreadable recovery input: {path}: {exc}") from exc


def save(path, value, *, immutable=False):
    path = Path(path)
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n"
    if len(raw) > MAX_BYTES or path.is_symlink():
        raise ValueError("oversized or symlink recovery output")
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".recovery-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def identity(value):
    if not isinstance(value, str) or not value or len(value) > 80 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in value):
        raise ValueError("invalid recovery identity")
    return value


def validate_config(cfg):
    for name in ("interval", "stable_seconds", "deadline", "render_cooldown"):
        value = cfg[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"invalid {name}")
    if type(cfg["render_slots"]) is not int or not 1 <= cfg["render_slots"] <= 8:
        raise ValueError("invalid render slots")
    if not isinstance(cfg["probes"], list) or len(cfg["probes"]) > 12:
        raise ValueError("invalid probe inventory")
    seen = {"host-pressure", "seed-lock"}
    for p in cfg["probes"]:
        for name in ("name", "owner", "backup"):
            identity(p[name])
        if p["name"] in seen:
            raise ValueError("duplicate probe identity")
        seen.add(p["name"])
        if not isinstance(p["argv"], list) or not p["argv"] or any(not isinstance(x, str) or not x or "\0" in x for x in p["argv"]):
            raise ValueError("invalid probe argv")
        if not 0 < p["timeout"] <= 30:
            raise ValueError("invalid probe timeout")
    return cfg


def run_probe(argv, *, timeout=5, cwd=None, env=None, output_limit=65536, pass_fds=()):
    started = time.monotonic()
    child_env = dict(os.environ if env is None else env)
    for name in ("NOTIFY_SOCKET", "WATCHDOG_PID", "WATCHDOG_USEC"):
        child_env.pop(name, None)
    try:
        # The watchdog has its own process lifetime: an outer renderer killing
        # this wrapper cannot leave its original renderer running indefinitely.
        supervised = [sys.executable, str(Path(__file__).with_name("probe_supervisor.py")),
                      "--parent", str(os.getpid()), "--timeout", str(timeout)]
        for descriptor in pass_fds:
            supervised.extend(["--hold-fd", str(descriptor)])
        supervised.extend(["--", *argv])
        process = subprocess.Popen(supervised, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                   pass_fds=pass_fds)
    except OSError as exc:
        return {"ok": None, "code": None, "stdout": "", "stderr": str(exc), "seconds": time.monotonic() - started}
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    failure = ""
    try:
        with selectors.DefaultSelector() as selector:
            for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)
            while selector.get_map():
                remaining = started + timeout + 1 - time.monotonic()
                if remaining <= 0:
                    failure = "timeout; "
                    break
                for key, _ in selector.select(min(.05, remaining)):
                    data = os.read(key.fd, min(65536, output_limit + 1))
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        buffer = buffers[key.data]
                        produced = len(buffer) + len(data)
                        buffer.extend(data[:max(0, output_limit - len(buffer))])
                        if produced > output_limit:
                            failure = "output limit exceeded; "
                            break
                if failure:
                    break
            if not failure:
                try:
                    process.wait(timeout=max(.001, started + timeout + 1 - time.monotonic()))
                except subprocess.TimeoutExpired:
                    failure = "timeout; "
    finally:
        if process.poll() is None:
            try:
                # Let the independent helper kill its child's separate group.
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            failure = "process termination unresolved; "
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
        process.stdout.close()
        process.stderr.close()
    if process.returncode in (124, -signal.SIGKILL, 128 + signal.SIGKILL):
        failure = failure or "timeout supervisor terminated command; "
    return {"ok": None if failure else process.returncode == 0, "code": process.returncode,
            "stdout": buffers["stdout"].decode("utf-8", "replace"),
            "stderr": failure + buffers["stderr"].decode("utf-8", "replace"),
            "seconds": time.monotonic() - started}


def pressure(proc=Path("/proc")):
    try:
        def full(name):
            rows = (proc / "pressure" / name).read_text().splitlines()
            row = next(x for x in rows if x.startswith("full "))
            return float(dict(x.split("=", 1) for x in row.split()[1:])["avg60"])
        mem = {}
        for line in (proc / "meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            mem[key] = int(value.split()[0])
        return {"memory_full": full("memory"), "io_full": full("io"),
                "available_ratio": mem["MemAvailable"] / mem["MemTotal"]}
    except (OSError, ValueError, KeyError, StopIteration, ZeroDivisionError):
        return {"memory_full": None, "io_full": None, "available_ratio": None}


SEED_LOCK_PROBE_WAIT = 3.0


def seed_lock_probe(home, wait=None):
    """Bounded availability only: never a claim that settlement succeeded.

    Short seed-lock holders (yields, wall ticks) release within seconds, so one
    instantaneous try reports contention as failure. Poll for up to ``wait``
    seconds; a holder that keeps the lock past that bound still fails.
    """
    wait = SEED_LOCK_PROBE_WAIT if wait is None else wait
    if not (wait >= 0):
        raise ValueError("seed lock probe wait must be nonnegative")
    started = time.monotonic()
    deadline = started + wait
    try:
        with (Path(home) / ".seed.lock").open("r+") as handle:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return {"ok": False, "reason": f"seed lock busy for {wait:g}s",
                                "seconds": time.monotonic() - started}
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                    # A late wake, or a holder released after the bound, must not
                    # turn into success: re-check the bound before any retry.
                    if time.monotonic() >= deadline:
                        return {"ok": False, "reason": f"seed lock busy for {wait:g}s",
                                "seconds": time.monotonic() - started}
    except OSError as exc:
        return {"ok": None, "reason": str(exc), "seconds": time.monotonic() - started}
    return {"ok": True, "reason": "lock acquired within bound; actual settlement untested",
            "seconds": time.monotonic() - started}


@contextmanager
def controller_lock(home):
    root = Path(home) / "recovery"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "controller.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield root


def validate_state(row):
    if (not isinstance(row, dict) or row.get("version") != 1 or type(row.get("checked_at")) not in (int, float)
            or not math.isfinite(row["checked_at"]) or not isinstance(row.get("functions"), dict)
            or row.get("state") not in {"GREEN", "DEGRADED", "UNKNOWN"}
            or not isinstance(row.get("capacity"), dict)
            or type(row["capacity"].get("limited")) is not bool
            or not 0 < row.get("max_age", 0) <= 600):
        raise ValueError("invalid recovery snapshot")
    for name, function in row["functions"].items():
        identity(name)
        if not isinstance(function, dict) or function.get("state") not in STATES or function.get("name") != name:
            raise ValueError("invalid functional recovery state")
        for key in ("episode", "checked_at", "first_failed", "passing_since", "restored_at", "resolver", "backup_requested", "backup_accepted_at"):
            if key not in function:
                raise ValueError("incomplete functional recovery state")
    if not {"host-pressure", "seed-lock"} <= set(row["functions"]):
        raise ValueError("missing recovery coverage")
    states = {r["state"] for r in row["functions"].values()}
    expected = "UNKNOWN" if "UNKNOWN" in states else "DEGRADED" if states - {"HEALTHY", "RESTORED"} else "GREEN"
    if row["state"] != expected:
        raise ValueError("inconsistent recovery aggregate")
    return row


def status(home, *, now=None):
    now = time.time() if now is None else now
    try:
        row = validate_state(read_json(Path(home) / "recovery/state.json"))
        if not 0 <= now - row["checked_at"] <= row["max_age"]:
            raise ValueError("recovery snapshot stale or future")
        return row
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"state": "UNKNOWN", "reason": str(exc)}


def event(root, payload, *, recipients=()):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    path = root / "events" / f"{digest}.json"
    if not path.exists():
        save(path, payload, immutable=True)
    for recipient in recipients:
        identity(recipient)
        out = root / "outbox" / f"{digest}-{recipient}.json"
        if not out.exists() and not (root / "delivered" / out.name).exists():
            save(out, {"event": digest, "recipient": recipient, "payload": payload}, immutable=True)
    return digest


def check(home, config, *, observation=None, now=None):
    now = time.time() if now is None else now
    cfg = validate_config(config)
    with controller_lock(home) as root:
        state_path = root / "state.json"
        if state_path.exists():
            previous = validate_state(read_json(state_path))
        elif (root / "initialized.json").exists():
            raise ValueError("recovery state lost; reconcile retained events before reset")
        else:
            previous = None
        obs = pressure() if observation is None else observation
        cap = policy.capacity((previous or {}).get("capacity"), now=now,
                              recover_seconds=cfg["stable_seconds"],
                              max_gap=cfg["interval"] * 3, **obs)
        control = seed_lock_probe(home)
        checks = {"host-pressure": {"ok": None if cap["state"] == "UNKNOWN" else not cap["limited"], "observation": obs},
                  "seed-lock": control}
        try:
            guard_row = read_json(root / "guard.json")
            guard_ok = 0 <= now - guard_row["checked_at"] <= max(45, cfg["interval"] * 3)
        except (ValueError, TypeError, KeyError):
            guard_ok = False
        checks["independent-guard"] = {"ok": True if guard_ok else None,
                                       "reason": "fresh independent execution" if guard_ok else "guard missing or stale"}
        specs = [{"name": name, "owner": "health", "backup": "genome"} for name in checks]
        for spec in cfg["probes"]:
            specs.append(spec)
            checks[spec["name"]] = run_probe(spec["argv"], timeout=spec["timeout"], cwd=home.parent)
        if previous and set(previous["functions"]) - set(checks):
            raise ValueError("probe coverage removed; restore inventory or provide reviewed equivalent coverage")
        functions = {}
        for spec in specs:
            name = spec["name"]
            old = (previous or {}).get("functions", {}).get(name)
            try:
                admission = read_json(root / "render-action.json")
                reduced = 0 <= now - admission["at"] <= cfg["interval"] * 3
            except (ValueError, KeyError, TypeError):
                reduced = False
            function = policy.transition(old, name=name, result=checks[name]["ok"], now=now,
                                         owner=spec["owner"], backup=spec["backup"],
                                         deadline=cfg["deadline"], stable_seconds=cfg["stable_seconds"],
                                         max_gap=cfg["interval"] * 3,
                                         mitigation=name == "host-pressure" and cap["limited"] and reduced)
            functions[name] = function
            change = lambda r: None if r is None else (r["episode"], r["state"], r["backup_requested"], r["backup_accepted_at"])
            if change(old) != change(function):
                recipients = [function["resolver"]]
                if function["backup_requested"]:
                    recipients.append(function["backup_requested"])
                if function["state"] not in {"HEALTHY", "RESTORED"} or old:
                    # Preserve every transition, but do not make transient
                    # passing samples in one still-open failure a firehose.
                    significant = (old is None or old["episode"] != function["episode"]
                                   or old["state"] in {"HEALTHY", "RESTORED"}
                                   or function["state"] in {"RESTORED", "UNKNOWN", "MITIGATED"}
                                   or old["backup_requested"] != function["backup_requested"]
                                   or old["backup_accepted_at"] != function["backup_accepted_at"])
                    event(root, {"at": now, "function": function, "check": checks[name]},
                          recipients=set(recipients) if significant else ())
        states = {r["state"] for r in functions.values()}
        row = {"version": 1, "checked_at": now, "max_age": max(60, cfg["interval"] * 3),
               "state": "UNKNOWN" if "UNKNOWN" in states else "DEGRADED" if states - {"HEALTHY", "RESTORED"} else "GREEN",
               "functions": functions, "checks": checks, "capacity": cap,
               "render_slots": cfg["render_slots"], "render_cooldown": cfg["render_cooldown"],
               "runtime": str(Path(__file__).resolve()), "config_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()}
        save(state_path, row)
        if not (root / "initialized.json").exists():
            save(root / "initialized.json", {"at": now}, immutable=True)
        sample = dict(obs, at=now, control_ok=control["ok"], control_seconds=control["seconds"], limited=cap["limited"])
        with (root / "samples.jsonl").open("a") as handle:
            handle.write(json.dumps(sample, allow_nan=False) + "\n")
        return row


def accept_incident(home, name, *, owner, now=None):
    now = time.time() if now is None else now
    with controller_lock(home) as root:
        row = validate_state(read_json(root / "state.json"))
        row["functions"][name] = policy.accept(row["functions"][name], owner=owner, now=now)
        event(root, {"at": now, "acceptance": row["functions"][name]}, recipients=[owner])
        save(root / "state.json", row)
        return row


def deliver(home, *, limit=1):
    """One bounded publisher; failures retain the exact event for reconciliation.

    append_runtime_once makes recovery after an uncertain timeout idempotent.
    It runs out of process and cannot block the recovery sampler.
    """
    root = Path(home) / "recovery"
    code = "from pathlib import Path; import sys; from mishe_tauftauf.feed import Feed; e=Feed(Path(sys.argv[1])).append_runtime_once('sensor/recovery',sys.argv[2]); print(e.sequence)"
    def last_attempt(path):
        try:
            return read_json(root / "attempts" / path.name)["at"]
        except (ValueError, KeyError):
            return 0
    def priority(path):
        attempt = last_attempt(path)
        if attempt:
            return (1, attempt, 0)
        payload = read_json(path)["payload"]
        capacity = payload.get("host_capacity", {})
        urgent = (capacity.get("state") == "CRITICAL" or capacity.get("resource_state") == "CRITICAL"
                  or capacity.get("oom_delta", 0) > 0 or bool(capacity.get("action_error")))
        # New host emergencies get one immediate attempt. Failure then rejoins
        # oldest-attempt order, so an undeliverable alert cannot starve others.
        return (0, 0 if urgent else 1, -capacity.get("checked_at", 0) if urgent else 0)
    for path in sorted((root / "outbox").glob("*.json"), key=priority)[:limit]:
        item = read_json(path)
        receipt_path = root / "delivered" / path.name
        if receipt_path.exists():
            # A crash between saving the delivered receipt and removing this
            # entry leaves both. The receipt is immutable and already says it was
            # delivered, so re-sending or re-saving would wedge the outbox on
            # FileExistsError forever (2026-10-29 19:43Z sentinel kill). Finish
            # the interrupted unlink only when the receipt names the same event.
            if read_json(receipt_path).get("event") == item["event"]:
                path.unlink()
            else:
                save(root / "delivery-last.json", {"at": time.time(), "event": item["event"],
                                                  "receipt": {"ok": False, "stderr": "delivered receipt names a different event"}})
            continue
        payload = item["payload"]
        if "function" in payload:
            function = payload["function"]
            detail = (f"Function {function['name']} is {function['state']}, episode {function['episode']}; "
                      f"resolver {function['resolver']}; backup requested {function.get('backup_requested') or 'none'}. "
                      "Inspect the retained probe and take one bounded corrective action; an overdue backup must explicitly accept before changing resolver.")
        elif "evaluation" in payload:
            report = payload["evaluation"]
            detail = f"Evaluation {report['verdict']}; monitoring {report['monitoring']}. Read the full comparison at {payload['artifact']}."
        elif "guard" in payload:
            detail = f"Independent recovery guard reports {payload['guard']['state']}. Inspect producer freshness and its service; missing observation stays UNKNOWN."
        elif "host_capacity" in payload:
            capacity = payload["host_capacity"]
            detail = (f"Combined host capacity is {capacity['state']}; new cgroup OOM kills {capacity.get('oom_delta', 0)}; "
                      f"action issue {capacity.get('action_error', 'none')}. Resolver Health; backup Genome; disposition within 300 seconds.")
        else:
            detail = "Inspect the retained recovery event and its exact action or acceptance evidence."
        text = (f"[dm] to={item['recipient']}\nRecovery event {item['event']}. {detail}\n"
                f"Evidence: {root / 'events' / (item['event'] + '.json')}. "
                f"Function restoration requires its probe; diagnosis or settlement is insufficient. Inspect {root / 'state.json'}.")
        receipt = run_probe([sys.executable, "-c", code, str(home), text], timeout=45)
        save(root / "attempts" / path.name, {"at": time.time(), "ok": receipt["ok"]})
        if receipt["ok"]:
            save(root / "delivered" / path.name, dict(item, receipt=receipt), immutable=True)
            path.unlink()
        else:
            save(root / "delivery-last.json", {"at": time.time(), "event": item["event"], "receipt": receipt})


def notify_watchdog():
    target = os.environ.get("NOTIFY_SOCKET")
    if target:
        address = "\0" + target[1:] if target.startswith("@") else target
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"WATCHDOG=1", address)


def guard(home, *, now=None):
    now = time.time() if now is None else now
    root = Path(home) / "recovery"
    producer = status(home, now=now)
    row = {"checked_at": now, "state": "UNKNOWN" if "reason" in producer else "FRESH",
           "functional_state": producer["state"],
           "scope": "independent producer schema/freshness only, not function restoration"}
    try:
        old = read_json(root / "guard.json")
    except ValueError:
        old = {}
    if old.get("state") != row["state"]:
        event(root, {"at": now, "guard": row, "producer": producer.get("reason")}, recipients=["health", "genome"])
    save(root / "guard.json", row)
    return row


def evaluate(home, *, now=None):
    now = time.time() if now is None else now
    root = Path(home) / "recovery"
    baseline = read_json(root / "baseline.json")
    after = []
    with (root / "samples.jsonl").open() as handle:
        for line in handle:
            if len(line) > 4096:
                raise ValueError("oversized recovery sample")
            row = json.loads(line)
            if row["at"] >= baseline["activated_at"]:
                after.append(row)
    report = dict(policy.compare(baseline["samples"], after), at=now,
                  activation_at=baseline["activated_at"],
                  monitoring=status(home, now=now)["state"],
                  scope="seed-lock availability, not actual wake settlement; pressure and uncontrolled before/after")
    if report["monitoring"] == "UNKNOWN":
        report["historical_comparison"] = report["verdict"]
        report["verdict"] = "UNKNOWN"
    if not after or now - after[-1]["at"] > 60 or any(b["at"] - a["at"] > 60 for a, b in zip(after, after[1:])):
        report["coverage"] = "UNKNOWN; missing or interrupted samples"
        report["verdict"] = "UNKNOWN"
    path = root / "evaluations" / f"{int(now)}.json"
    save(path, report, immutable=True)
    event(root, {"at": now, "evaluation": report, "artifact": str(path)}, recipients=["health", "genome", "witness"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("action", choices=["once", "watch", "status", "guard", "deliver", "evaluate", "accept"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--function")
    parser.add_argument("--owner")
    args = parser.parse_args()
    home = args.home.resolve()
    from .observations import validate_home
    validate_home(home)
    if args.action in {"status", "guard"}:
        row = guard(home) if args.action == "guard" else status(home)
        print(json.dumps(row, sort_keys=True))
        return 0 if row["state"] in {"GREEN", "FRESH"} else 3
    if args.action == "deliver":
        deliver(home)
        return 0
    if args.action == "evaluate":
        print(json.dumps(evaluate(home), sort_keys=True))
        return 0
    if args.action == "accept":
        accept_incident(home, args.function, owner=args.owner)
        return 0
    config = validate_config(read_json(args.config or home / "recovery/config.json"))
    while True:
        try:
            row = check(home, config)
            notify_watchdog()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print(f"UNKNOWN recovery controller: {exc}", file=sys.stderr, flush=True)
            if args.action == "once":
                return 3
        else:
            if args.action == "once":
                print(json.dumps(row, sort_keys=True))
                return 0 if row["state"] == "GREEN" else 3
        time.sleep(config["interval"])


if __name__ == "__main__":
    raise SystemExit(main())
