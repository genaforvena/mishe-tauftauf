"""Read-only local frontier scan; values are evidence, not authority."""

from __future__ import annotations

import json
import os
import time
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed

COMMANDS = ("rg", "git", "tmux", "python3", "systemctl", "journalctl", "ps", "df",
            "lsusb", "lspci", "sensors", "upower", "evtest")

KERNEL_FAULT = "Failed to resubmit video URB"

def _thermal_slots(root: Path) -> list[Path]:
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    slots: list[Path] = []
    for entry in entries:
        try:
            slots.extend(sorted(entry.glob("temp*_input"))[:16])
        except OSError:
            continue
    return slots



def _read(path: Path, limit: int = 65536) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return None

def _cpu_busy(path: Path = Path("/proc/stat"), samples: int = 2, interval: float = 0.1) -> dict:
    """Measure short-window host CPU busyness from aggregate /proc/stat deltas."""
    counters: list[list[int]] = []
    for index in range(samples):
        head = _read(path, 512)
        if head is None:
            return {}
        lines = head.splitlines()
        if not lines:
            return {}
        fields = lines[0].split()
        if not fields or fields[0] != "cpu" or len(fields) < 9:
            return {}
        if not all(token.isdigit() for token in fields[1:]):
            return {}
        counters.append([int(token) for token in fields[1:]])
        if index + 1 < samples:
            time.sleep(interval)
    deltas: list[list[int]] = []
    for previous, current in zip(counters, counters[1:]):
        if len(previous) != len(current) or any(now < before for before, now in zip(previous, current)):
            return {}
        deltas.append([now - before for before, now in zip(previous, current)])
    if not deltas:
        return {}
    # user/nice/system/idle/iowait/irq/softirq/steal; guest counters overlap user.
    total = sum(sum(delta[:8]) for delta in deltas)
    idle = sum(delta[3] + delta[4] for delta in deltas)
    if total <= 0 or idle > total:
        return {}
    busy = 100.0 * (total - idle) / total
    return {"busy": busy, "idle": 100.0 - busy}

TOP_CPU_ROWS = 8
"""Rows retained per scan; a later scan attributes an event from this bounded set."""

TOP_CPU_SAMPLE_SECONDS = 1.0
"""Window between the two /proc cpu reads. One clock tick is 1/100 s, so a one
second window resolves a rate in 1%-of-core steps; a shorter window drops a
sustained but modest consumer to zero ticks and discards it again."""

try:
    _CLOCK_TICK = os.sysconf("SC_CLK_TCK")
except (AttributeError, OSError, ValueError):
    _CLOCK_TICK = 100


def _cpu_jiffies(pid: int) -> int | None:
    """utime + stime for one process in clock ticks, or None when unreadable.

    `comm` may contain spaces and parentheses, so the field offset is taken past
    the last closing parenthesis rather than by splitting the whole line.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    fields = raw[raw.rindex(")") + 1:].split()
    # utime and stime follow state, ppid, pgrp, sid, tty, tpgid, flags and four
    # fault counters, so they are fields 11 and 12 after `comm`.
    if len(fields) < 13:
        return None
    try:
        return int(fields[11]) + int(fields[12])
    except ValueError:
        return None


def _ps_processes() -> dict[int, dict[str, object]]:
    """One bounded `ps` read: pid, lifetime %CPU, elapsed seconds and the command."""
    try:
        result = subprocess.run(["ps", "-eo", "pid,pcpu,etimes,args", "--no-headers"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    processes: dict[int, dict[str, object]] = {}
    for line in result.stdout.splitlines():
        columns = line.split(None, 3)
        if len(columns) < 4:
            continue
        pid_text, pcpu_text, etimes_text, args_text = columns
        if not pid_text.isdigit():
            continue
        try:
            pcpu = float(pcpu_text)
            etimes = int(etimes_text)
        except ValueError:
            continue
        processes[int(pid_text)] = {"pid": int(pid_text), "pcpu": pcpu,
                                    "etimes": etimes,
                                    "args": " ".join(args_text.split())[:160]}
    return processes


def _rate_key(row: dict[str, object]) -> tuple[float, float]:
    """Rank by the sampled rate; the lifetime average only breaks its ties."""
    rate = row.get("rate")
    return ((rate if rate is not None else -1.0), row["pcpu"])


def _row_text(row: dict[str, object]) -> str:
    """Render one retained row, naming the key that produced it."""
    if row.get("rate") is None:
        return (f"pid={row['pid']} rate=unmeasured pcpu={row['pcpu']:.1f}% "
                f"etimes={row['etimes']}s {row['args']}")
    return (f"pid={row['pid']} rate={row['rate']:.1f}% pcpu={row['pcpu']:.1f}% "
            f"etimes={row['etimes']}s {row['args']}")


def _top_cpu_processes(rows: int = TOP_CPU_ROWS,
                       sample_seconds: float = TOP_CPU_SAMPLE_SECONDS) -> list[dict[str, object]]:
    """Retain the busiest processes so a later scan can attribute, not just detect.

    Aggregate counters notice a pressure event only after it ends, and `ps` polled
    afterwards sees nothing when the cause was sub-second. Keeping a bounded sample
    in every persisted scan makes a sustained event attributable from the scan set
    alone (discover wake 13786, evidenced senses 13884 -> 14053).

    Ranking on `ps` %CPU does not serve that goal: it is a *lifetime* average
    (cputime / etimes), so a long-running process cannot move it within one busy
    second while a young process reaches the top rows by birth, and it is a
    division by zero at `etimes` 0. A two-sample /proc cpu delta rates the window
    the event actually lives in; the lifetime average and `etimes` stay as columns
    for context. A process that burned no tick in the window still ranks below one
    that did, and its lifetime average orders those ties rather than deciding them.
    """
    processes = _ps_processes()
    if not processes:
        return []
    first = {pid: _cpu_jiffies(pid) for pid in processes}
    time.sleep(sample_seconds)
    for pid, info in processes.items():
        later = _cpu_jiffies(pid)
        start = first.get(pid)
        if start is None or later is None or later < start:
            # Unreadable, or the pid was recycled between the two reads; either
            # way the delta is not about this process, so it stays unmeasured
            # rather than being reported as idle in the window.
            continue
        info["rate"] = 100.0 * (later - start) / _CLOCK_TICK / sample_seconds
    return sorted(processes.values(), key=_rate_key, reverse=True)[:rows]
def _journal_error_window(past_minutes: int = 10, limit: int = 400) -> dict:
    """Read the journal's own error class over a bounded window.

    Returns a neutral count of one well-known kernel-driver message, never a
    degradation verdict: on this host the uvcvideo resubmit rate is idle-camera
    status noise and is inversely related to camera use (discover wake 116), so
    the sample names every contributing endpoint instead of implying health.
    A full-boot scan is unbounded and is never attempted.
    """
    try:
        primary = subprocess.run(
            ["journalctl", "-b", "-p", "err", "--since", f"-{past_minutes}min",
             "-o", "cat", "--no-pager"],
            capture_output=True, text=True, timeout=10)
        if primary.returncode != 0:
            primary = subprocess.run(
                ["journalctl", "-b", "-p", "err", "-n", str(limit),
                 "-o", "cat", "--no-pager"],
                capture_output=True, text=True, timeout=10)
        window = primary.stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return {}
    faults = [line for line in window if KERNEL_FAULT in line]
    if not faults:
        return {"state": "verified" if window else "unknown",
                "sample": (f"last-{past_minutes}min kernel-error-count=0"
                           if window else "journal error window unavailable")}
    endpoints = sorted({line.split(": Failed", 1)[0].strip() for line in faults})
    names = ",".join(endpoints)
    return {"state": "verified",
            "sample": f"last-{past_minutes}min kernel-error-count={len(faults)} endpoints={names}"}


def sample(home: Path) -> dict[str, object]:
    """Take bounded reads; never open input event streams or record key content."""
    observed: list[dict[str, object]] = []
    for command in COMMANDS:
        found = shutil.which(command)
        observed.append({"id": f"command.{command}", "state": "available" if found else "unavailable",
                         "sample": found or "not on PATH", "kind": "declaration"})
    journal = _journal_error_window()
    if journal:
        observed.append({"id": "sense.journal.kernel-error-rate", "kind": "read", **journal})
    else:
        observed.append({"id": "sense.journal.kernel-error-rate", "kind": "read",
                         "state": "unknown", "sample": "journal error window unavailable"})

    loadavg = _read(Path("/proc/loadavg"), 256)
    observed.append({"id": "sense.proc.loadavg", "state": "verified" if loadavg else "unknown",
                     "sample": loadavg.strip() if loadavg else "read unavailable", "kind": "read"})
    memory = _read(Path("/proc/meminfo"), 8192)
    available = re.search(r"^MemAvailable:\s+(\d+ kB)$", memory or "", re.MULTILINE)
    observed.append({"id": "sense.proc.memory-available", "state": "verified" if available else "unknown",
                     "sample": available.group(1) if available else "read unavailable", "kind": "read"})
    pressure = _read(Path("/proc/pressure/memory"), 512)
    pressure_lines = [line.strip() for line in (pressure or "").splitlines()
                      if re.match(r"^(some|full) avg10=[0-9]+(?:\.[0-9]+)? avg60=[0-9]+(?:\.[0-9]+)? "
                                  r"avg300=[0-9]+(?:\.[0-9]+)? total=[0-9]+$", line.strip())]
    pressure_ok = any(line.startswith("some ") for line in pressure_lines)
    observed.append({"id": "sense.proc.memory-pressure",
                     "state": "verified" if pressure_ok else "unknown",
                     "sample": "; ".join(pressure_lines) if pressure_ok else "pressure data unavailable",
                     "kind": "read"})
    cpu_pressure = _read(Path("/proc/pressure/cpu"), 512)
    cpu_pressure_lines = [line.strip() for line in (cpu_pressure or "").splitlines()
                          if re.match(r"^some avg10=[0-9]+(?:\.[0-9]+)? avg60=[0-9]+(?:\.[0-9]+)? "
                                      r"avg300=[0-9]+(?:\.[0-9]+)? total=[0-9]+$", line.strip())]
    cpu_pressure_ok = bool(cpu_pressure_lines)
    observed.append({"id": "sense.proc.cpu-pressure",
                     "state": "verified" if cpu_pressure_ok else "unknown",
                     "sample": "; ".join(cpu_pressure_lines) if cpu_pressure_ok else "pressure data unavailable",
                     "kind": "read"})
    io_pressure = _read(Path("/proc/pressure/io"), 512)
    io_pressure_lines = [line.strip() for line in (io_pressure or "").splitlines()
                         if re.match(r"^(some|full) avg10=[0-9]+(?:\.[0-9]+)? avg60=[0-9]+(?:\.[0-9]+)? "
                                     r"avg300=[0-9]+(?:\.[0-9]+)? total=[0-9]+$", line.strip())]
    io_pressure_ok = any(line.startswith("some ") for line in io_pressure_lines)
    observed.append({"id": "sense.proc.io-pressure",
                     "state": "verified" if io_pressure_ok else "unknown",
                     "sample": "; ".join(io_pressure_lines) if io_pressure_ok else "pressure data unavailable",
                     "kind": "read"})
    try:
        disk = os.statvfs(home.parent)
        free_bytes = disk.f_bavail * disk.f_frsize
        observed.append({"id": "sense.disk.free", "state": "verified", "sample": free_bytes, "kind": "read"})
        observed.append({"id": "sense.disk.inodes-available", "state": "verified",
                         "sample": disk.f_favail, "kind": "read"})
    except OSError:
        observed.append({"id": "sense.disk.free", "state": "unknown",
                         "sample": "statvfs unavailable", "kind": "read"})
        observed.append({"id": "sense.disk.inodes-available", "state": "unknown",
                         "sample": "statvfs unavailable", "kind": "read"})
    busy = _cpu_busy()
    if busy:
        observed.append({"id": "sense.proc.cpu-busy", "state": "verified",
                         "sample": f"short-window={0.1:.1f}s busy={busy['busy']:.1f}% idle={busy['idle']:.1f}%"
                                   + (" high" if busy["busy"] >= 85.0 else ""),
                         "kind": "read"})
    else:
        observed.append({"id": "sense.proc.cpu-busy", "state": "unknown",
                         "sample": "/proc/stat cpu fields unavailable", "kind": "read"})
    top_cpu = _top_cpu_processes()
    if top_cpu:
        measured = [row for row in top_cpu if row.get("rate") is not None]
        observed.append({"id": "sense.proc.top-cpu", "state": "verified",
                         "sample": "; ".join(_row_text(row) for row in top_cpu)
                                   + (f" key=delta-rate-over-{TOP_CPU_SAMPLE_SECONDS:.1f}s"
                                      if measured else " key=ps-lifetime"),
                         "processes": top_cpu, "kind": "read"})
    else:
        # A missing ps or a failed read is a transient, not a structural absence: the
        # command is declared available and a retry can produce a sample.
        observed.append({"id": "sense.proc.top-cpu", "state": "unknown",
                         "sample": "ps process sample unavailable", "kind": "read"})
    interrupts = _read(Path("/proc/interrupts"), 65536)
    keyboard = []
    for line in (interrupts or "").splitlines():
        if re.search(r"i8042|atkbd|keyboard", line, re.IGNORECASE):
            columns = line.split(":", 1)
            if len(columns) == 2:
                numbers = []
                for token in columns[1].split():
                    if not token.isdigit():
                        break
                    numbers.append(int(token))
                if numbers:
                    keyboard.append(sum(numbers))
    if keyboard:
        observed.append({"id": "sense.input.keyboard-interrupt-count",
                         "state": "verified", "sample": sum(keyboard), "kind": "counter"})
    elif interrupts is None:
        observed.append({"id": "sense.input.keyboard-interrupt-count", "state": "unknown",
                         "sample": "counter unreadable; /proc/interrupts unavailable",
                         "kind": "counter"})
    else:
        # The interrupt source is readable but names no keyboard device, so no
        # counter exists on this host. That is a structural absence, not a
        # transient read failure: retrying the same read cannot produce one.
        observed.append({"id": "sense.input.keyboard-interrupt-count", "state": "unavailable",
                         "sample": "no keyboard interrupt source on this host", "kind": "counter"})
    wakeup_root = Path("/sys/class/wakeup")
    wakeup_counts = []
    if wakeup_root.is_dir():
        for path in list(wakeup_root.glob("*/event_count"))[:128]:
            value = _read(path, 64)
            if value and value.strip().isdigit():
                wakeup_counts.append(int(value.strip()))
    observed.append({"id": "sense.sys.wakeup-count",
                     "state": "verified" if wakeup_counts else "unknown",
                     "sample": sum(wakeup_counts) if wakeup_counts else "counter unavailable",
                     "kind": "counter"})
    hwmon_root = Path("/sys/class/hwmon")
    temperatures: list[tuple[str, dict[str, str | None]]] = []
    for slot in _thermal_slots(hwmon_root):
        value = _read(slot, 64)
        if value is None or not value.strip().lstrip("-").isdigit():
            continue
        millidegrees = int(value.strip())
        if millidegrees < -273150 or millidegrees > 200000:
            # An out-of-range value is a stuck or absent sensor, not a
            # temperature; skipping keeps the reported sample honest.
            continue
        chip = _read(slot.parent / "name", 64)
        name = chip.strip() if chip else slot.parent.name
        channel = re.fullmatch(r"temp(\d+)_input", slot.name)
        if channel is None:
            continue
        index = channel.group(1)
        label_text = _read(slot.with_name(f"temp{index}_label"), 64)
        label = label_text.strip() if label_text and label_text.strip() else None
        channel_id = f"temp{index}" + (f"({label})" if label else "")
        device_path = slot.parent / "device"
        try:
            parent = str(device_path.resolve()) if device_path.exists() else None
        except (OSError, RuntimeError):
            parent = None
        channel_identity = f"{name}:{channel_id}"
        temperatures.append((f"{channel_identity}={millidegrees / 1000.0:.1f}C",
                             {"channel": channel_identity, "parent": parent}))
    thermal_identity = [identity for _, identity in temperatures]
    observed.append({"id": "sense.thermal.hwmon", "state": "verified" if temperatures else "unknown",
                     "sample": ", ".join(sample_text for sample_text, _ in temperatures)
                     if temperatures else "no readable hwmon temperature sensor",
                     "identity": thermal_identity, "kind": "read"})
    session = os.environ.get("MISHE_SEED_SESSION")
    if not session:
        try:
            session = (home / ".seed-raised").read_text(encoding="utf-8").split()[0]
        except (OSError, IndexError):
            session = None
    if session:
        try:
            result = subprocess.run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
                                    capture_output=True, text=True, timeout=2)
            names = sorted(result.stdout.splitlines()) if result.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired):
            names = []
    else:
        names = []
    observed.append({"id": "sense.tmux.windows", "state": "verified" if names else "unknown",
                     "sample": names or "session unavailable", "kind": "read"})
    return {"created": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "node": os.uname().nodename, "observations": observed}


def scan(home: Path) -> Path:
    previous = latest(home)
    snapshot = sample(home)
    root = home / "discovery"
    root.mkdir(parents=True, exist_ok=True)
    stamp = snapshot["created"].replace(":", "").replace("-", "")
    artifact = root / f"scan-{stamp}-{os.getpid()}.json"
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    artifact.write_text(payload, encoding="utf-8")
    latest_path = root / "latest.json"
    temporary = root / f".latest-{os.getpid()}.tmp"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, latest_path)
    readings = snapshot["observations"]
    available = [str(item["id"]).removeprefix("command.") for item in readings
                 if item["kind"] == "declaration" and item["state"] == "available"]
    unavailable = [str(item["id"]).removeprefix("command.") for item in readings
                   if item["kind"] == "declaration" and item["state"] == "unavailable"]
    verified = [item for item in readings if item["kind"] != "declaration" and item["state"] == "verified"]
    unknown = [item for item in readings if item["kind"] != "declaration" and item["state"] != "verified"]
    def signature(data: dict[str, object] | None) -> dict[str, tuple[str, str]]:
        if data is None:
            return {}
        result = {}
        for item in data.get("observations", []):
            identifier = str(item["id"])
            state = str(item["state"])
            sample_text = str(item["sample"])
            if item["kind"] == "declaration" or state != "verified":
                detail = sample_text
            elif identifier == "sense.proc.cpu-busy":
                detail = "high" if re.search(r"(?:^|\s)high(?:$|\s)", sample_text) else "not-high"
            elif identifier == "sense.thermal.hwmon":
                detail = json.dumps(item.get("identity", []), ensure_ascii=False, sort_keys=True)
            else:
                detail = ""
            result[identifier] = (state, detail)
        return result

    current_signature = signature(snapshot)
    old_signature = signature(previous)
    changed = [name for name in current_signature if current_signature[name] != old_signature.get(name)]
    if previous is not None and not changed:
        return artifact
    change_details = []
    for name in changed:
        if previous is None:
            change_details.append(name)
            continue
        old_reading = next((item for item in previous.get("observations", [])
                            if item["id"] == name), None)
        new_reading = next((item for item in readings if item["id"] == name), None)
        if old_reading and new_reading and old_reading["state"] == new_reading["state"] == "verified":
            if name == "sense.proc.cpu-busy":
                old_class = "high" if re.search(
                    r"(?:^|\s)high(?:$|\s)", str(old_reading["sample"])) else "not-high"
                new_class = "high" if re.search(
                    r"(?:^|\s)high(?:$|\s)", str(new_reading["sample"])) else "not-high"
                change_details.append(
                    f"{name} class {old_class} -> {new_class} (short sample: "
                    f"{old_reading['sample']} -> {new_reading['sample']})")
                continue
            if name == "sense.thermal.hwmon":
                def describe_identity(reading):
                    return "; ".join(
                        f"channel {item.get('channel', 'unknown')}, "
                        f"parent device {item.get('parent') or 'unavailable'}"
                        for item in reading.get("identity", []) if isinstance(item, dict)
                    ) or "unavailable"
                old_identity = describe_identity(old_reading)
                new_identity = describe_identity(new_reading)
                change_details.append(f"{name} identity {old_identity} -> {new_identity}")
                continue
        change_details.append(name)
    change_text = ("Initial baseline." if previous is None else
                   "Material changes: " + "; ".join(change_details) + ".")
    lines = [f"[discovery] Read-only scan at {snapshot['created']} on {snapshot['node']}.",
             change_text,
             f"Available commands ({len(available)}): {', '.join(available) or 'none'}.",
             f"Unavailable commands ({len(unavailable)}): {', '.join(unavailable) or 'none'}.",
             "Verified readings: " + ("; ".join(f"{item['id']} = {item['sample']}" for item in verified) or "none") + ".",
             "Unknown readings: " + ("; ".join(f"{item['id']} — {item['sample']}" for item in unknown) or "none") + ".",
             f"Full sample: {artifact.resolve()}.",
             "Next: senses should verify useful unknown readings or record why the source is unavailable; "
             "discover should seek one new useful read."]
    Feed(home).append("discover", "\n".join(lines))
    return artifact


def latest(home: Path) -> dict[str, object] | None:
    path = home / "discovery" / "latest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


RENEWAL_MAX_AGE_SECONDS = 600.0
"""Renew before the panes' 900-second freshness gate, leaving 300 seconds of margin."""


def scan_age(home: Path) -> float | None:
    """Age of the newest scan in seconds, or None when its timestamp is unavailable."""
    snapshot = latest(home)
    if snapshot is None:
        return None
    try:
        created = datetime.fromisoformat(str(snapshot["created"]).replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return None
    return (datetime.now(timezone.utc) - created).total_seconds()


def renew_scan(home: Path, max_age_seconds: float = RENEWAL_MAX_AGE_SECONDS) -> Path | None:
    """Renew a missing or old scan without requiring a resident mind wake.

    Return the new artifact path, or None while the latest scan is fresh.
    scan() updates latest.json and deduplicates unchanged states in the feed.
    """
    age = scan_age(home)
    if age is not None and age <= max_age_seconds:
        return None
    return scan(home)
