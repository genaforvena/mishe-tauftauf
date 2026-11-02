"""Literal recovery boundary: bounded local files, Linux probes and commands.

The control loop never reads the chat tape or takes its locks. Notification
publication is a separate, timed process with an immutable local outbox.

Fencing per Chubby §2.4 and Kleppmann 2016 § "Making the lock safe with fencing"
(Burrows et al. OSDI '06 PDF sha256 0747c84a49d32744a2744e2f2951b05fb7a0829ce44afb0b700833eb2bb3ecdd, /tmp/chubby-osdi06.pdf 117687 bytes 16 pp., fetched 2026-11-02; Kleppmann HTML 42424 bytes sha256 8cf7f237e65c same date). A sequencer is {lock_name, mode, generation_number} minted on each acquisition and checked by the store: the server accepts a write only if token > highest_seen[token.lock] (Chubby: "contains the name, mode, generation"; "validity can be checked against the most recent sequencer observed"; Kleppmann: "a number that increases every time a client acquires the lock ... the storage server remembers that it has already processed a write with a higher token number (34), and so it rejects [33]"). Chubby doc HTML 141836 bytes sha256 e88e46bf287b same source. Fencing is storage-side validation; lock-delay <=60s is imperfect.
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

# SRE Ch.6 histogram: exponential boundaries ~×3 scaled to recovery seconds
# covering observed 0.5-86s holds (see SEED_HOLDER_STUCK_SECONDS comment).
DEFAULT_HISTOGRAM_BOUNDARIES = (0.5, 1.5, 5, 15, 30, 90, 120, 300)


def histogram_buckets(samples, boundaries=None):
    """Bucket counts for latency samples per SRE Ch.6 exponential histogram.

    ``boundaries`` are strictly increasing upper edges in seconds. Bucket i holds
    count of samples with boundaries[i-1] <= t < boundaries[i] (i=0 uses 0).
    Overflow bucket len(boundaries) holds t >= boundaries[-1]. Returned dict has
    ``counts`` (bucket_index -> count), ``total`` and ``boundaries``.
    Pure function, no I/O. Retains sorted samples for exact tail percentile
    so p95/p99 do not average the 86 s excursion away when n is small.
    """
    if boundaries is None:
        boundaries = DEFAULT_HISTOGRAM_BOUNDARIES
    boundaries = tuple(float(b) for b in boundaries)
    if not boundaries or any(not math.isfinite(b) or b <= 0 for b in boundaries):
        raise ValueError("invalid histogram boundaries")
    if any(boundaries[i] >= boundaries[i + 1] for i in range(len(boundaries) - 1)):
        raise ValueError("histogram boundaries must be strictly increasing")
    # Validate samples are finite numbers; reject NaN/inf so percentiles are honest.
    for s in samples:
        if type(s) not in (int, float) or not math.isfinite(float(s)):
            raise ValueError(f"invalid histogram sample: {s!r}")
        if float(s) < 0:
            raise ValueError(f"negative histogram sample: {s!r}")
    counts: dict[int, int] = {}
    sorted_samples = sorted(float(s) for s in samples)
    for s in samples:
        v = float(s)
        idx = 0
        for b in boundaries:
            if v < b:
                break
            idx += 1
        counts[idx] = counts.get(idx, 0) + 1
    return {"counts": counts, "total": len(samples), "boundaries": boundaries, "samples": sorted_samples}


def latency_histogram_summary(histogram, *, boundaries=None):
    """Derive p50/p75/p95/p99 from histogram; tail-visible without hiding 86 s.

    ``histogram`` is the dict returned by :func:`histogram_buckets`. When
    ``samples`` are retained, percentiles are exact via sorted samples using
    ``ceil(p*n)-1`` (matching ``scan_freshness.aoi_summary``). Buckets remain
    the published SRE view so the distribution is visible; the percentiles are
    not bucket-edge approximations that would map the 86 s tail to 30 s.
    Returns ``{"n", "p50", "p75", "p95", "p99", "buckets", "boundaries", "reason"}``.
    """
    if isinstance(histogram, dict) and "counts" in histogram:
        counts = dict(histogram["counts"])
        total = int(histogram["total"])
        bnds = tuple(histogram.get("boundaries", boundaries or DEFAULT_HISTOGRAM_BOUNDARIES))
        samples = histogram.get("samples")
    else:
        raise ValueError("histogram must be result of histogram_buckets")
    if boundaries is not None:
        bnds = tuple(float(b) for b in boundaries)
    n = total
    if n == 0:
        return {"n": 0, "p50": None, "p75": None, "p95": None, "p99": None,
                "buckets": counts, "boundaries": bnds, "reason": "no samples"}
    # Exact percentiles when samples retained; bucket edges otherwise.
    if isinstance(samples, (list, tuple)) and len(samples) == n:
        sorted_s = sorted(float(s) for s in samples)
        def pct(p):
            idx = math.ceil(p * n) - 1
            idx = max(0, min(idx, n - 1))
            return sorted_s[idx]
        p50, p75, p95, p99 = pct(0.50), pct(0.75), pct(0.95), pct(0.99)
    else:
        edges = [0.0] + list(bnds)
        max_idx = max(counts) if counts else -1
        upper = max(max_idx, len(bnds))
        cumul = 0
        p_targets = {0.50: None, 0.75: None, 0.95: None, 0.99: None}
        thresholds = {p: math.ceil(p * n) for p in p_targets}
        for idx in range(upper + 1):
            cumul += counts.get(idx, 0)
            for p in list(p_targets):
                if p_targets[p] is None and cumul >= thresholds[p]:
                    p_targets[p] = edges[idx] if idx < len(edges) else edges[-1]
        for p in p_targets:
            if p_targets[p] is None:
                p_targets[p] = edges[max_idx] if max_idx >= 0 else 0.0
        p50, p75, p95, p99 = p_targets[0.50], p_targets[0.75], p_targets[0.95], p_targets[0.99]
    reason = "ok" if n >= 10 else f"small N={n}; p99 = max(bucket) edge-aware, buckets published"
    return {"n": n, "p50": p50, "p75": p75, "p95": p95, "p99": p99,
            "buckets": counts, "boundaries": bnds, "reason": reason}


def slo_check(histogram, *, p=0.95, limit=30.0):
    """Evaluate percentile against limit; extra observability, not a second latch.

    Returns ``(ok, reason)`` like guard's vocabulary: ok True when percentile
    is below limit, False when at or above, None when no samples or unknown.
    Does not change deadline/stable_seconds thresholds.
    """
    if type(p) not in (int, float) or not 0 < p < 1:
        raise ValueError("p must be in (0,1)")
    if type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0:
        raise ValueError("invalid limit")
    summary = latency_histogram_summary(histogram)
    if summary["n"] == 0:
        return (None, "no samples; SLO not evaluated")
    key = {0.50: "p50", 0.75: "p75", 0.95: "p95", 0.99: "p99"}.get(round(float(p), 2))
    if key is not None:
        percentile = summary[key]
    else:
        # exact quantile from retained samples at arbitrary p
        samples = histogram.get("samples") if isinstance(histogram, dict) else None
        if isinstance(samples, (list, tuple)) and len(samples) == summary["n"]:
            sorted_s = sorted(float(s) for s in samples)
            idx = math.ceil(float(p) * summary["n"]) - 1
            idx = max(0, min(idx, summary["n"] - 1))
            percentile = sorted_s[idx]
        else:
            percentile = summary["p95"] if summary["p95"] is not None else None
    if percentile is None:
        return (None, "percentile unavailable; small N")
    if percentile < float(limit):
        return (True, f"p{int(round(p*100))}={percentile:g}s < limit {limit:g}s n={summary['n']} buckets={summary['buckets']}")
    return (False, f"p{int(round(p*100))}={percentile:g}s >= limit {limit:g}s n={summary['n']} buckets={summary['buckets']}")


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


SEED_LOCK_WINDOW_SECONDS = 10.0
# Provisional bound on one holder's hold age. Measured holds (health sample
# artifacts/health-seed-lock-sample-20261101T095149Z/samples.json, 2026-11-01): seed yield 15.8-30.2 s,
# seed run 0.5-2.1 s, one hold of 86 s. 120 s is the restore's own stability window, so a holder that
# outlasts it cannot be waited out by restoration. Health confirms or revises this from later samples.
SEED_HOLDER_STUCK_SECONDS = 120.0


def stamp_seed_holder(handle, now=None):
    """Record acquisition time and pid in the seed lock file while the caller holds the lock.

    Best effort: a stamp failure never blocks the lock holder's work. An unstamped busy lock
    stays UNKNOWN in seed_lock_probe, which is the conservative reading.
    """
    record = {"pid": os.getpid(), "argv": list(sys.argv[:4]),
              "acquired_at": time.time() if now is None else now}
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(record) + "\n")
        handle.flush()
    except (OSError, ValueError):
        pass


def clear_seed_holder(handle):
    """Remove the holder stamp before the caller releases the lock. Best effort."""
    try:
        handle.seek(0)
        handle.truncate()
        handle.flush()
    except (OSError, ValueError):
        pass


def _flock_holder_pids(handle):
    """Pids holding a BSD flock on this lock file, read from /proc/locks; None when unreadable.

    /proc/locks names the holder of each flock by device and inode; a stamp is trusted only when
    its pid is exactly this set, so a stale stamp left by a dead or different holder is never read.
    """
    try:
        st = os.fstat(handle.fileno())
        key = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
        lines = Path("/proc/locks").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    pids = set()
    for line in lines:
        parts = line.split()
        # "1: FLOCK  ADVISORY  WRITE 3273 fc:00:6819524 0 EOF"
        if len(parts) >= 6 and parts[1] == "FLOCK" and parts[4].isdigit() and parts[5] == key:
            pids.add(int(parts[4]))
    return pids


def _judged_holder_age(home, handle, now):
    """Return (age, None) for the current holder's stamp, or (None, why) when it cannot be trusted.

    Trusted only when the stamp's pid is the one flock holder of this lock file (which also proves it
    is alive) and its acquisition time is not in the future. The 120 s bound is provisional and
    the wall clock is used because the stamp is read by another process.
    """
    try:
        record = json.loads((Path(home) / ".seed.lock").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "no readable stamp"
    if not isinstance(record, dict):
        return None, "no readable stamp"
    pid, acquired = record.get("pid"), record.get("acquired_at")
    if type(pid) is not int or pid <= 0:
        return None, "stamp names no pid"
    if type(acquired) not in (int, float) or not math.isfinite(acquired):
        return None, "stamp names no acquisition time"
    holders = _flock_holder_pids(handle)
    if holders is None:
        return None, "flock holder not readable"
    if holders != {pid}:
        return None, f"stamp pid {pid} is not the flock holder {sorted(holders)}"
    if acquired > now + 1.0:
        return None, "stamp is in the future; wall clock stepped"
    return max(0.0, now - acquired), None


def _holder_fact(handle, why):
    """Name the busy lock's flock holder(s) from /proc: pid, program basename and PYTHONPATH release.

    Read-only and bounded. It makes an unstamped UNKNOWN precise without changing its verdict:
    the holder is evidence of a live process, not a claim that the holder is current or healthy.
    """
    pids = _flock_holder_pids(handle)
    if pids is None:
        return f"HELD, holder not readable from /proc/locks; stamp: {why}"
    if not pids:
        return f"HELD, no flock holder visible in /proc/locks; stamp: {why}"
    facts = []
    for pid in sorted(pids):
        # Program basename only: full argv can carry secrets and is written into recovery records.
        try:
            argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].decode("utf-8", "replace")
            program = Path(argv0).name or "unknown"
        except OSError:
            program = "unreadable"
        release = "unreadable"
        try:
            env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            found = [item.decode("utf-8", "replace") for item in env if item.startswith(b"PYTHONPATH=")]
            release = found[0][len("PYTHONPATH="):] if found else "unset"
        except OSError:
            pass
        facts.append(f"HELD, holder pid {pid}, program={program!r}, stamp: {why}; PYTHONPATH={release}")
    return "; ".join(facts)


def seed_lock_probe(home, window=SEED_LOCK_WINDOW_SECONDS, poll=0.2, stuck_after=SEED_HOLDER_STUCK_SECONDS):
    """Availability only: never a claim that settlement succeeded.

    A wake holds the seed lock for the length of a turn, which can run for minutes. Busy is
    contention, not failure: the probe waits up to the window for release and passes if the lock
    frees in time. A lock still busy at the end of the window is judged by its stamped holder age:
    under ``stuck_after`` it is a live holder and passes stability; over it, the holder is treated as
    stuck and fails. A busy lock with no readable stamp stays UNKNOWN (ok None): the probe cannot
    tell a live turn from a stuck holder without the stamp, and it never claims a failure it cannot see.
    """
    started = time.monotonic()
    try:
        with (Path(home) / ".seed.lock").open("r+") as handle:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if time.monotonic() - started >= window:
                        seconds = time.monotonic() - started
                        age, why = _judged_holder_age(home, handle, time.time())
                        if age is None:
                            return {"ok": None, "reason": f"seed lock held for {window:.1f}s window; availability not measured: {why}; {_holder_fact(handle, why)}",
                                    "seconds": seconds}
                        if age > stuck_after:
                            return {"ok": False, "holder_age": age, "seconds": seconds,
                                    "reason": f"seed lock holder stamped {age:.1f}s ago exceeds the {stuck_after:g}s bound; stuck holder suspected"}
                        return {"ok": True, "holder_age": age, "seconds": seconds,
                                "reason": f"seed lock held by a live holder {age:.1f}s (bound {stuck_after:g}s); stability passing, settlement untested"}
                    time.sleep(poll)
                    continue
                fcntl.flock(handle, fcntl.LOCK_UN)
                break
    except OSError as exc:
        return {"ok": None, "reason": str(exc), "seconds": time.monotonic() - started}
    return {"ok": True, "reason": "lock available; actual settlement untested", "seconds": time.monotonic() - started}


@contextmanager
def controller_lock(home):
    root = Path(home) / "recovery"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "controller.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield root


def fencing_sequencer(lock: str, generation: int, mode: str = "exclusive") -> dict:
    """Opaque sequencer per Chubby §2.4: {name, mode, generation}. No I/O."""
    if not isinstance(lock, str) or not lock:
        raise ValueError("invalid lock name")
    if type(generation) is not int:
        raise ValueError("generation must be int")
    if not isinstance(mode, str) or mode not in {"exclusive", "shared"}:
        raise ValueError("mode must be exclusive or shared")
    if generation < 0 or generation >= 2**63:
        raise ValueError("generation out of range")
    return {"name": lock, "mode": mode, "generation": int(generation)}


def next_generation(prev: int) -> int:
    """Strictly increasing generation; ValueError on overflow or non-int."""
    if type(prev) is not int:
        raise ValueError("prev generation must be int")
    if prev < -1 or prev >= 2**63 - 1:
        raise ValueError("generation overflow or out of range")
    return int(prev) + 1


def is_sequencer_valid(token: dict, highest_seen: dict) -> bool:
    """True iff token.generation > highest_seen.get(token.name, -1) and mode matches."""
    if not isinstance(token, dict) or not isinstance(highest_seen, dict):
        raise ValueError("invalid sequencer arguments")
    name = token.get("name")
    mode = token.get("mode")
    gen = token.get("generation")
    if not isinstance(name, str) or not isinstance(mode, str) or type(gen) is not int:
        raise ValueError("invalid sequencer token")
    if mode not in {"exclusive", "shared"}:
        raise ValueError("invalid sequencer mode")
    prev = highest_seen.get(name, -1)
    if type(prev) is not int:
        raise ValueError("highest_seen value must be int")
    return gen > prev


def check_fencing_token(token_generation: int, highest_seen_generation: int) -> bool:
    """Kleppmann check: accept iff token > highest_seen. Pure, no files."""
    if type(token_generation) is not int or type(highest_seen_generation) is not int:
        raise ValueError("fencing generations must be int")
    return token_generation > highest_seen_generation


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


def host_cgroup(root, now, lead=0.0):
    """The capacity controller's latest cgroup reading, for the restore policy's memory-high dimension.

    A missing, unreadable or stale controller row yields NaN, which the policy classifies UNKNOWN.
    Restoration never reads a fallback NORMAL from pressure alone (2026-11-01 blind spot).
    The controller writes every ~5 s, and `now` is taken before the seed-lock wait, so a fresh row can
    be written up to `lead` seconds after `now` (2026-11-02: lead ~3 s put the row in the future and
    every such pass read UNKNOWN). A row further ahead than the measured wait still fails closed.
    """
    unknown = {"current": math.nan, "high": math.nan}
    try:
        row = read_json(Path(root) / "host-capacity.json")
        observed = row["observation"]
        age = now - row["checked_at"]
        if not (-lead <= age <= row.get("max_age", 20)):
            return unknown
        return {"current": float(observed["current"]), "high": float(observed["high"])}
    except (OSError, ValueError, KeyError, TypeError):
        return unknown


def check(home, config, *, observation=None, now=None):
    now = time.time() if now is None else now
    cfg = validate_config(config)
    # The seed-lock probe may wait a full window; sample it before taking the controller lock so
    # other controller callers are not blocked behind an observation that is not a state write.
    control = seed_lock_probe(home)
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
                              max_gap=cfg["interval"] * 3, **obs,
                              **host_cgroup(root, now, lead=float(control.get("seconds") or 0.0) + 1.0))
        checks = {"host-pressure": {"ok": None if cap["state"] == "UNKNOWN" else not cap["limited"], "observation": obs},
                  "seed-lock": control}
        try:
            guard_row = read_json(root / "guard.json")
            # `now` predates the seed-lock wait; a guard row written during that wait is fresh, not future.
            # Bound the allowed lead by the measured wait, so a far-future row still fails closed.
            lead = float(control.get("seconds") or 0.0) + 1.0
            guard_ok = -lead <= now - guard_row["checked_at"] <= max(45, cfg["interval"] * 3)
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
        # Fencing observable: bump controller.generation if present (under controller.lock).
        # Absent => no fencing field, byte-identical. Does not create a new recovery function.
        fencing_info = None
        try:
            gen_path = root / "controller.generation"
            if gen_path.exists():
                raw = read_json(gen_path)
                if isinstance(raw, dict) and "generation" in raw:
                    prev = raw["generation"]
                elif type(raw) is int:
                    prev = raw
                else:
                    prev = None
                if type(prev) is int and 0 <= prev < 2**63 - 1:
                    new_gen = next_generation(prev)
                    save(gen_path, {"generation": new_gen, "at": now})
                    fencing_info = {"generation": new_gen, "highest_seen": prev}
                elif prev is not None:
                    fencing_info = {"reason": "invalid-generation", "prev": str(prev)}
        except (OSError, ValueError, TypeError, KeyError):
            fencing_info = None
        states = {r["state"] for r in functions.values()}
        row = {"version": 1, "checked_at": now, "max_age": max(60, cfg["interval"] * 3),
               "state": "UNKNOWN" if "UNKNOWN" in states else "DEGRADED" if states - {"HEALTHY", "RESTORED"} else "GREEN",
               "functions": functions, "checks": checks, "capacity": cap,
               "render_slots": cfg["render_slots"], "render_cooldown": cfg["render_cooldown"],
               "runtime": str(Path(__file__).resolve()), "config_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()}
        if fencing_info is not None:
            checks["controller-lock"] = {"fencing": fencing_info}
            row["checks"] = checks
        save(state_path, row)
        if not (root / "initialized.json").exists():
            save(root / "initialized.json", {"at": now}, immutable=True)
        sample = dict(obs, at=now, control_ok=control["ok"], control_seconds=control["seconds"], limited=cap["limited"])
        if fencing_info is not None and "generation" in fencing_info:
            sample["fencing_generation"] = fencing_info["generation"]
            sample["fencing_highest_seen"] = fencing_info["highest_seen"]
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
            # Only a successful receipt for this exact event, recipient and payload
            # counts as delivery. Any other persisted receipt is conflicting evidence:
            # keep the outbox entry, record the conflict, and rotate it to the back
            # of the queue so it cannot starve later events (Witness 131741).
            saved = read_json(receipt_path)
            if (saved.get("event") == item["event"] and saved.get("receipt", {}).get("ok") is True
                    and saved.get("recipient") == item["recipient"] and saved.get("payload") == item["payload"]):
                path.unlink()
            else:
                reason = ("delivered receipt names a different event" if saved.get("event") != item["event"]
                          else "delivered receipt is not a successful match for this recipient and payload")
                save(root / "delivery-last.json", {"at": time.time(), "event": item["event"],
                                                  "receipt": {"ok": False, "stderr": reason}})
                save(root / "attempts" / path.name, {"at": time.time(), "ok": False, "conflict": reason})
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
            observed = capacity.get("checked_at")
            age = (time.time() - observed
                   if isinstance(observed, (int, float)) and math.isfinite(observed) else None)
            if age is None or age < 0:
                # A future-dated observation is not evidence of current state (clock skew or a corrupt row).
                freshness = "observation time missing or in the future; treat as UNKNOWN, not current"
                due = "unknown"
            else:
                due = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(observed + 300))
                if age > capacity.get("max_age", 60):
                    freshness = (f"STALE observation at {time.strftime('%H:%M:%SZ', time.gmtime(observed))}, "
                                 f"age {int(age)}s > max_age {capacity.get('max_age')}s; historical, not current")
                else:
                    freshness = f"observed {time.strftime('%H:%M:%SZ', time.gmtime(observed))}, age {int(age)}s"
            detail = (f"Combined host capacity was {capacity['state']} ({freshness}); new cgroup OOM kills {capacity.get('oom_delta', 0)}; "
                      f"action issue {capacity.get('action_error', 'none')}. Resolver Health; backup Genome; "
                      f"disposition due {due} (300 seconds after observation).")
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
