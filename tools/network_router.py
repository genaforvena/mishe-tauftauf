#!/usr/bin/env python3
"""Router reading for the network window: per-device Wi-Fi signal, uplink state and uptime.

Read-only. One `ssh router` run prints uptime, both radios' client lists, the DHCP leases (for
names) and the WAN status; nothing on the router is changed. Every run appends one row to
SITE/network/router.jsonl, failed runs included, so a dead probe shows as UNKNOWN, never as a calm
network.

Privacy: a client is identified by a short hash of its MAC; the name is the hostname the household's
own DHCP lease gives it (or "unnamed device"). No addresses, no traffic content. Rows older than two
days are dropped on write, so the file stays small.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_boundary as nb

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
LOG_NAME = "router.jsonl"
KEEP_S = 2 * 86400
TIMEOUT_S = 20
BANDS = {"ra0": "2.4", "rax0": "5"}  # radio name -> GHz; ra0 is the 2.4 GHz radio, rax0 the 5 GHz one
REMOTE = (
    'echo "UPTIME $(cut -d" " -f1 /proc/uptime)"; '
    "for d in ra0 rax0; do echo \"RADIO $d\"; iwinfo $d assoclist 2>/dev/null; done; "
    'echo LEASES; cat /tmp/dhcp.leases 2>/dev/null; echo WAN; '
    'ubus call network.interface.wan status 2>/dev/null; echo LOAD; cat /proc/loadavg 2>/dev/null; '
    'grep -E "^MemAvailable:" /proc/meminfo 2>/dev/null; echo LINKS; ip -s link show 2>/dev/null'
)


def client_id(mac):
    """A short, stable, non-reversible id for a MAC address."""
    return hashlib.sha256(mac.lower().encode()).hexdigest()[:8]


def parse(stdout):
    """Decode the router's combined output into a row body. ValueError when the output is not what we asked for."""
    return nb.decode_router_output(stdout, BANDS, client_id)


def _row(now, body=None, error=None):
    row = {"ts": round(now, 1), "ok": body is not None}
    if body is not None:
        row.update(body)
    else:
        row["error"] = error
    return row


def measure(now=None):
    """One read-only router pass. Returns a row; never raises."""
    now = time.time() if now is None else now
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router", REMOTE],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return _row(now, error=type(exc).__name__ + ": " + str(exc)[:120])
    if r.returncode != 0:
        return _row(now, error=f"ssh exit {r.returncode}: " + nb.stderr_summary(r.stderr))
    try:
        return _row(now, body=parse(r.stdout))
    except ValueError as exc:
        return _row(now, error="unreadable router output: " + str(exc)[:120])


def trim(rows, now):
    """Rows no older than KEEP_S."""
    return [row for row in rows if isinstance(row.get("ts"), (int, float)) and now - row["ts"] <= KEEP_S]


def main(argv=None):
    ap = argparse.ArgumentParser(description=nb.doc_summary(__doc__))
    ap.add_argument("--home", default=str(SITE))
    args = nb.parse_argv(ap, argv)
    path = Path(args.home) / "network" / LOG_NAME
    now = time.time()
    row = measure(now)
    try:
        kept = trim(nb.decode_router_rows(path.read_text()), now)
    except OSError:
        kept = []
    kept.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in kept))
    tmp.replace(path)
    print(json.dumps(row, sort_keys=True))
    return 0 if row["ok"] else 3  # 3 = UNKNOWN in the duty contract, not RED


if __name__ == "__main__":
    sys.exit(main())
