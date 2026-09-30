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
KERNEL_FAULT = "Failed to resubmit video URB"

    """List bounded hwmon temperature inputs under root."""
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


def _overlay_egress_rate(root: Path = Path("/sys/class/net"), interval: float = 0.1) -> dict:
    """Measure tailscale0 transmit and receive rates over a bounded counter window."""
    interface = root / "tailscale0"
    stats = interface / "statistics"
    counters = {"tx_bytes": "egress", "rx_bytes": "ingress"}
    before: dict[str, int] = {}
    for name in counters:
        value = _read(stats / name, 64)
        if value is None or not value.strip().isdigit():
            state = "unavailable" if not interface.exists() else "unknown"
            return {"state": state, "sample": f"tailscale0 {name} unavailable"}
        before[name] = int(value.strip())
    time.sleep(interval)
    rates: list[str] = []
    for name, direction in counters.items():
        value = _read(stats / name, 64)
        if value is None or not value.strip().isdigit():
            return {"state": "unknown", "sample": f"tailscale0 {name} unavailable"}
        delta = int(value.strip()) - before[name]
        if delta < 0:
            return {"state": "unknown", "sample": f"tailscale0 {name} counter decreased"}
        rates.append(f"{direction}={delta / interval:.0f} B/s")
    return {"state": "verified",
            "sample": f"short-window={interval:.1f}s " + " ".join(rates)}
def _kernel_error_window(past_minutes: int = 10, limit: int = 400) -> dict:
    """Sample the live kernel error rate over a bounded journal window.

    A line is an error only when the journal itself classified it as one and
    it names the failing kernel device, so a busy userspace stream cannot read
    as a kernel fault. A full-boot scan is unbounded and is never attempted.
    """
    try:
        result = subprocess.run(
            ["journalctl", "-b", "-p", "err", "--since", f"-{past_minutes}min", "-o", "cat", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        # Bounded fallback from the tail of the current boot.
        try:
            fallback = subprocess.run(
                ["journalctl", "-b", "-p", "err", "-n", str(limit), "-o", "cat", "--no-pager"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if fallback.returncode != 0:
            return {}
        lines = fallback.stdout.splitlines()
        window = f"tail-{limit}"
    else:
        lines = result.stdout.splitlines()
        window = f"last-{past_minutes}min"
    faults = [line for line in lines if KERNEL_FAULT in line]
    if not faults and lines:
        return {"state": "verified", "sample": f"{window} kernel-error-count=0", "kind": "read"}
    if not faults:
        return {}
    # "uvcvideo 1-6:1.1: Failed to ..." -> device=uvcvideo
    device = faults[0].split(":", 1)[0].split()[0] or "unknown-device"
    return {"state": "verified",
            "sample": (f"{window} kernel-error-count={len(faults)} "
                       f"device={device} tail={str(faults[-1])[:48]}"),
            "kind": "read"}



def sample(home: Path) -> dict[str, object]:
    """Take bounded reads; never open input event streams or record key content."""
    observed: list[dict[str, object]] = []
    journal = _kernel_error_window()
    if journal:
        observed.append({"id": "sense.journal.kernel-error-rate", **journal})
    else:
        observed.append({"id": "sense.journal.kernel-error-rate", "state": "unknown",
                         "sample": "journal error window unavailable", "kind": "read"})


    for command in COMMANDS:
        found = shutil.which(command)
        observed.append({"id": f"command.{command}", "state": "available" if found else "unavailable",
                         "sample": found or "not on PATH", "kind": "declaration"})

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
    temperatures: list[str] = []
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
        temperatures.append(f"{name}={millidegrees / 1000.0:.1f}C")
    observed.append({"id": "sense.thermal.hwmon", "state": "verified" if temperatures else "unknown",
                     "sample": ", ".join(temperatures)
                     if temperatures else "no readable hwmon temperature sensor",
                     "kind": "read"})
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
        return {str(item["id"]): (str(item["state"]), str(item["sample"])
                                    if item["state"] == "unknown" or item["kind"] == "declaration" else "")
                for item in data.get("observations", [])}

    current_signature = signature(snapshot)
    old_signature = signature(previous)
    changed = [name for name in current_signature if current_signature[name] != old_signature.get(name)]
    if previous is not None and not changed:
        return artifact
    change_text = ("Initial baseline." if previous is None else
                   "Changed states or unknown reasons: " + ", ".join(changed) + ".")
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
