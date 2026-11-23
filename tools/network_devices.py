#!/usr/bin/env python3
"""Per-device reachability for the network window: a few pings to each named household device.

The gateway and internet probe cannot see one slow device, so this reading pings the household's
own named devices on the LAN (a fixed short list) and appends one row per run to
SITE/network/devices.jsonl. A failed ping is recorded as failed, never as a pass, so a dead probe
shows as UNKNOWN in the detector, not as a calm house.

Bounded: a few ICMP echoes per device with a short per-run timeout, no scanning, no content.
Privacy: the row carries the household's device name and derived numbers only, no addresses.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_boundary as nb  # noqa: E402  - all text decoding lives in the boundary

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
LOG_NAME = "devices.jsonl"
PINGS = 5
PING_TIMEOUT_S = 25
KEEP_S = 2 * 86400

# name the household recognises -> LAN address. Addresses stay in this file, never in the rows.
DEVICES = (
    ("iMac-Rozalia", "192.168.8.214"),
)

def ping(address):
    """Run a few pings to one device; a failure to run is a failed row, never a pass."""
    cmd = ["ping", "-n", "-q", "-c", str(PINGS), "-W", "2", address]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=PING_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "why": f"ping did not run: {type(exc).__name__}"}
    return nb.decode_ping(out.stdout, out.returncode)


def neighbour_state(address, run=subprocess.run):
    """The kernel's IPv4 neighbour state for one device: REACHABLE/STALE/DELAY/PROBE, FAILED, or UNKNOWN.

    FAILED means the LAN address did not answer ARP at all, which separates a device that has
    left the Wi-Fi from one that is associated but silent to pings. An unreadable table is UNKNOWN.
    """
    try:
        out = run(["ip", "-4", "neigh", "show", address], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"
    if out.returncode != 0:
        return "UNKNOWN"
    return nb.decode_neighbour(out.stdout)


ROUTER_STALE_S = 15 * 60  # same bound as network_view.STALE_ROUTER_S: three missed producer runs


def _plain(name):
    """Case, spaces and punctuation removed: the router's hostname may differ in case from the device name."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def wifi_state(home, name, now=None):
    """Whether the router's newest reading lists the device as a Wi-Fi client: ASSOCIATED, ABSENT, or UNKNOWN.

    Read from SITE/network/router.jsonl only (no extra SSH). Only a fresh, successful router read counts:
    a stopped router producer must not explain a silent device with hours-old data. A device associated
    to Wi-Fi but silent to ARP and ping is asleep or its IP stack is suspended; a device absent from
    Wi-Fi has left.
    """
    now = time.time() if now is None else now
    rows = nb.decode_router_rows(_read_text(home / "network" / "router.jsonl"))
    if not rows:
        return "UNKNOWN"
    last = rows[-1]
    if not last.get("ok") or not 0 <= now - last["ts"] <= ROUTER_STALE_S:
        return "UNKNOWN"
    if not isinstance(last.get("clients"), list):
        return "UNKNOWN"
    names = {_plain(c.get("name")) for c in last["clients"] if isinstance(c, dict) and c.get("name")}
    return "ASSOCIATED" if _plain(name) in names else "ABSENT"


def _read_text(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def _read_rows(path):
    try:
        text = Path(path).read_text()
    except OSError:
        return []
    return nb.decode_json_lines(text)


def run(home, now=None):
    now = time.time() if now is None else now
    result = {}
    for name, address in DEVICES:
        result[name] = ping(address)
        result[name]["neigh"] = neighbour_state(address)
        result[name]["wifi"] = wifi_state(home, name)
    row = {
        "ts": now,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "devices": result,
    }
    path = home / "network" / LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [r for r in _read_rows(path) if isinstance(r.get("ts"), (int, float)) and now - r["ts"] <= KEEP_S]
    kept.append(row)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in kept))
    os.replace(tmp, path)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, default=SITE)
    a = nb.parse_argv(ap, None)
    row = run(a.home)
    failed = [name for name, res in row["devices"].items() if not res.get("ok")]
    print(f"network-devices: {len(row['devices']) - len(failed)}/{len(row['devices'])} answered"
          + (f"; no reply from {', '.join(failed)}" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
