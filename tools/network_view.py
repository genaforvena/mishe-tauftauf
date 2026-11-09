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
SLOW_WEB_MS = 1500
SLOW_DNS_MS = 500
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


def web_ms(row, name):
    """Total time of one secure web check in this row, or None when it did not load (or was not measured)."""
    check = ((row or {}).get("internet") or {}).get(name)
    return check.get("total_ms") if isinstance(check, dict) and check.get("ok") else None


def verdict(row, a):
    if row is None or a is None or a > STALE_S:
        return "UNKNOWN (no fresh probe)"
    if "internet" not in row:
        return "UNKNOWN (probe is from before the secure web check)"
    gw = row.get("gateway")
    if gw is None or gw["loss_pct"] >= 50:
        return "DOWN inside the home (router/LAN not answering)"
    if not any(web_ms(row, n) is not None for n in row["internet"]):
        return "DOWN: router answers, but no secure website does"
    if gw["loss_pct"] > 0:
        return "DEGRADED: loss to the router"
    best = min(v for v in (web_ms(row, n) for n in row["internet"]) if v is not None)
    if best > SLOW_WEB_MS or (row.get("dns_ms") or 0) > SLOW_DNS_MS:
        return "DEGRADED: slow " + ("DNS" if (row.get("dns_ms") or 0) > SLOW_DNS_MS else "web pages")
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


def gateway_line(data):
    cur = (data[-1].get("gateway") if data else None) or {}
    vals = [((r.get("gateway") or {}).get("avg_ms")) for r in data]
    loss = [((r.get("gateway") or {}).get("loss_pct")) for r in data]
    now = f"{cur['avg_ms']:.1f} ms" if "avg_ms" in cur else "UNKNOWN"
    lossnow = f"{cur['loss_pct']:.0f}% loss" if "loss_pct" in cur else ""
    worst = max([v for v in loss if v is not None], default=None)
    return (f"  {'router (LAN)':<22}{now:>10}  {lossnow:<9} {spark(vals)}  "
            f"worst loss {('%.0f%%' % worst) if worst is not None else 'UNKNOWN'}")


def web_line(label, name, data):
    vals = [web_ms(r, name) for r in data]
    cur = vals[-1] if vals else None
    now = f"{cur:.0f} ms" if cur is not None else "UNKNOWN"
    failed = sum(1 for v in vals[-40:] if v is None)
    return f"  {label:<22}{now:>10}  {spark(vals)}  failed in last {min(len(vals), 40)}: {failed}"


def draw():
    data = rows()
    last = data[-1] if data else None
    a = age(last) if last else None
    out = [f"MISHE NETWORK  {time.strftime('%H:%M:%SZ', time.gmtime())}   derived numbers only; UNKNOWN when not measured", ""]
    out.append(f"  HOME INTERNET: {verdict(last, a)}")
    out.append(f"  last probe: {'%ds ago' % a if a is not None else 'never'}   (one probe a minute; sparklines = last {min(len(data), 40)} probes)")
    out.append("")
    out.append("  ROUTER AND LOCAL LINK (ping, average of 5)")
    out.append(gateway_line(data))
    out.append("")
    out.append("  INTERNET (secure web pages, full time to finish)")
    out.append(web_line("gstatic.com", "gstatic", data))
    out.append(web_line("cloudflare.com", "cloudflare", data))
    dns = [r.get("dns_ms") for r in data]
    out.append(f"  DNS lookup            {('%.0f ms' % dns[-1]) if dns and dns[-1] is not None else 'UNKNOWN':>10}  {spark(dns)}")
    out.append("")
    out.append("  SPEED         UNKNOWN (no bounded speed test has been built yet)")
    out.append("  WI-FI         UNKNOWN (clients and signal not read yet)")
    rf = router_facts()
    out.append("  ROUTER        " + (rf or "UNKNOWN (ssh read failed)") + "   (read-only)")
    out.append("")
    out.append("  note: pings to public addresses are not used: the router answers them itself,")
    out.append("  so they look fast even when the internet is down. Internet checks are secure page loads.")
    return "\n".join(out)


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(draw())
    else:
        while True:
            sys.stdout.write("\x1b[H\x1b[2J" + draw() + "\n")
            sys.stdout.flush()
            time.sleep(10)
