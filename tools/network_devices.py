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
import re
import subprocess
import sys
import time
from pathlib import Path

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
LOG_NAME = "devices.jsonl"
PINGS = 5
PING_TIMEOUT_S = 25
KEEP_S = 2 * 86400

# name the household recognises -> LAN address. Addresses stay in this file, never in the rows.
DEVICES = (
    ("iMac-Rozalia", "192.168.8.214"),
)

RTT_LINE = re.compile(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)")
LOSS_LINE = re.compile(r"(\d+(?:\.\d+)?)% packet loss")


def decode_ping(stdout, returncode):
    """{"ok", "loss_pct", "avg_ms", "max_ms"} from ping output, or {"ok": False, "why": ...}.

    A reply that carries no round-trip figures is not a pass: it is reported as not ok.
    """
    loss = LOSS_LINE.search(stdout)
    rtt = RTT_LINE.search(stdout)
    if loss is None:
        return {"ok": False, "why": "ping output unreadable"}
    loss_pct = float(loss.group(1))
    if rtt is None or loss_pct >= 100.0:
        return {"ok": False, "loss_pct": loss_pct, "why": "no reply"}
    return {
        "ok": True,
        "loss_pct": loss_pct,
        "avg_ms": float(rtt.group(2)),
        "max_ms": float(rtt.group(3)),
    }


def ping(address):
    """Run a few pings to one device; a failure to run is a failed row, never a pass."""
    cmd = ["ping", "-n", "-q", "-c", str(PINGS), "-W", "2", address]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=PING_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "why": f"ping did not run: {type(exc).__name__}"}
    return decode_ping(out.stdout, out.returncode)


def read_rows(path):
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def run(home, now=None):
    now = time.time() if now is None else now
    result = {}
    for name, address in DEVICES:
        result[name] = ping(address)
    row = {
        "ts": now,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "devices": result,
    }
    path = home / "network" / LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [r for r in read_rows(path) if isinstance(r.get("ts"), (int, float)) and now - r["ts"] <= KEEP_S]
    kept.append(row)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in kept))
    os.replace(tmp, path)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--home", type=Path, default=SITE)
    a = ap.parse_args()
    row = run(a.home)
    failed = [name for name, res in row["devices"].items() if not res.get("ok")]
    print(f"network-devices: {len(row['devices']) - len(failed)}/{len(row['devices'])} answered"
          + (f"; no reply from {', '.join(failed)}" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
