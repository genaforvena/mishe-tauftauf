#!/usr/bin/env python3
"""Live window: how the home network and connection are doing, in plain words.

Reads the rows the `network-probe` duty appends to SITE/network/probe.jsonl and, read-only,
a few facts from the router over ssh (never writes there).  Anything not measured, or too old,
is shown as UNKNOWN.  The network mind improves this file.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_boundary as nb

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
PROBE = SITE / "network" / "probe.jsonl"
SPARK = "▁▂▃▄▅▆▇█"
STALE_S = 300
ROUTER_EVERY_S = 60
_router = {"at": 0.0, "text": None}


def rows(limit=1440):
    try:
        return nb.decode_rows(PROBE.read_text(), limit)
    except OSError:
        return []


def age(row):
    return nb.row_age_seconds(row)


def spark(values, width=40):
    values = [v for v in values if v is not None][-width:]
    if not values:
        return "(no data)"
    hi = max(values) or 1
    return "".join(SPARK[min(7, int(v / hi * 7.999))] for v in values)


def verdict(row, a):
    if row is None or a is None or a > STALE_S:
        return "UNKNOWN (no fresh probe)"
    gw, cf, gg = row.get("gateway"), row.get("cloudflare"), row.get("google")
    if gw is None or gw["loss_pct"] >= 50:
        return "DOWN inside the home (router/LAN not answering)"
    if (cf is None or cf["loss_pct"] >= 50) and (gg is None or gg["loss_pct"] >= 50):
        return "DOWN: router answers, internet does not"
    bad = [n for n, r in (("gateway", gw), ("cloudflare", cf), ("google", gg)) if r and r["loss_pct"] > 0]
    slow = row.get("https_ms") is None or row["https_ms"] > 1500 or (row.get("dns_ms") or 0) > 500
    if bad or slow:
        return "DEGRADED: " + ("loss to " + ",".join(bad) if bad else "slow DNS/HTTPS")
    return "OK"


def router_facts():
    if time.time() - _router["at"] < ROUTER_EVERY_S:
        return _router["text"]
    _router["at"] = time.time()
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router",
                            "cut -d' ' -f1 /proc/uptime; cut -d' ' -f1-3 /proc/loadavg; "
                            "ubus call network.interface.wan status | grep -E '\"up\"|\"uptime\"' | tr -d ' \\n'"],
                           capture_output=True, text=True, timeout=12)
        days, load, wan = nb.decode_router(r.stdout)
        _router["text"] = f"router up {days:.1f} days, load {load}, WAN {'up' if wan else 'UNKNOWN/down'}"
    except (OSError, ValueError, subprocess.SubprocessError):
        _router["text"] = None
    return _router["text"]


def line(label, key, data):
    cur = data[-1].get(key) if data else None
    vals = [(r.get(key) or {}).get("avg_ms") if isinstance(r.get(key), dict) else None for r in data]
    loss = [(r.get(key) or {}).get("loss_pct") if isinstance(r.get(key), dict) else None for r in data]
    now = f"{cur['avg_ms']:.1f} ms" if isinstance(cur, dict) and "avg_ms" in cur else "UNKNOWN"
    lossnow = f"{cur['loss_pct']:.0f}% loss" if isinstance(cur, dict) else ""
    worst = max([v for v in loss if v is not None], default=None)
    return f"  {label:<22}{now:>10}  {lossnow:<9} {spark(vals)}  worst loss {('%.0f%%' % worst) if worst is not None else 'UNKNOWN'}"


def draw():
    data = rows()
    last = data[-1] if data else None
    a = age(last) if last else None
    out = [f"MISHE NETWORK  {time.strftime('%H:%M:%SZ', time.gmtime())}   derived numbers only; UNKNOWN when not measured", ""]
    out.append(f"  HOME INTERNET: {verdict(last, a)}")
    out.append(f"  last probe: {'%ds ago' % a if a is not None else 'never'}   (one probe a minute; sparklines = last {min(len(data), 40)} probes)")
    out.append("")
    out.append("  LATENCY (average of 5 pings) and LOSS")
    out.append(line("router (gateway)", "gateway", data))
    out.append(line("internet 1.1.1.1", "cloudflare", data))
    out.append(line("internet 8.8.8.8", "google", data))
    out.append("")
    dns = [r.get("dns_ms") for r in data]
    https = [r.get("https_ms") for r in data]
    out.append(f"  DNS lookup            {('%.0f ms' % dns[-1]) if dns and dns[-1] is not None else 'UNKNOWN':>10}  {spark(dns)}")
    out.append(f"  web page (HTTPS)      {('%.0f ms' % https[-1]) if https and https[-1] is not None else 'UNKNOWN':>10}  {spark(https)}")
    out.append("")
    out.append("  SPEED         UNKNOWN (no bounded speed test has been built yet)")
    out.append("  WI-FI         UNKNOWN (clients and signal not read yet)")
    rf = router_facts()
    out.append("  ROUTER        " + (rf or "UNKNOWN (ssh read failed)") + "   (read-only)")
    out.append("")
    out.append("  note: 1.1.1.1 answering in ~1 ms looks like the router answering for it; the network mind is to check what that means")
    return "\n".join(out)


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(draw())
    else:
        while True:
            sys.stdout.write("\x1b[H\x1b[2J" + draw() + "\n")
            sys.stdout.flush()
            time.sleep(10)
