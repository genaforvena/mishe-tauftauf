#!/usr/bin/env python3
"""One small network probe: append a derived row to SITE/network/probe.jsonl.

Latency/loss to the default gateway and two public resolvers, DNS lookup time and
one HTTPS fetch time.  No content is captured.  A probe that cannot run exits 3
(UNKNOWN); it never reports a quiet network.
"""
import argparse, json, re, socket, subprocess, sys, time
from pathlib import Path

TARGETS = {"gateway": None, "cloudflare": "1.1.1.1", "google": "8.8.8.8"}
HTTPS = "https://www.gstatic.com/generate_204"
DNS_NAME = "www.wikipedia.org"


def gateway():
    out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
    m = re.search(r"default via (\S+)", out)
    return m.group(1) if m else None


def ping(host, count=5):
    if not host:
        return None
    r = subprocess.run(["ping", "-n", "-c", str(count), "-W", "2", "-i", "0.2", host],
                       capture_output=True, text=True, timeout=30)
    loss = re.search(r"([\d.]+)% packet loss", r.stdout)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", r.stdout)
    if not loss:
        return None
    row = {"loss_pct": float(loss.group(1))}
    if rtt:
        row.update(min_ms=float(rtt.group(1)), avg_ms=float(rtt.group(2)), max_ms=float(rtt.group(3)),
                   jitter_ms=float(rtt.group(4)))
    return row


def dns_ms():
    t = time.monotonic()
    try:
        socket.getaddrinfo(DNS_NAME, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    return round((time.monotonic() - t) * 1000, 1)


def https_ms():
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", "10", "-w", "%{http_code} %{time_total}", HTTPS],
                       capture_output=True, text=True, timeout=15)
    parts = r.stdout.split()
    if len(parts) == 2 and parts[0] in ("204", "200"):
        return round(float(parts[1]) * 1000, 1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    a = ap.parse_args()
    gw = gateway()
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "gateway_ip": gw}
    try:
        for name, host in TARGETS.items():
            row[name] = ping(gw if name == "gateway" else host)
        row["dns_ms"] = dns_ms()
        row["https_ms"] = https_ms()
    except (OSError, subprocess.SubprocessError):
        return 3
    out = Path(a.home) / "network" / "probe.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    down = [n for n in TARGETS if row.get(n) is None or row[n]["loss_pct"] >= 50]
    if down == ["cloudflare", "google"] or (gw and row.get("gateway") and row["gateway"]["loss_pct"] >= 50):
        print("network down: " + ",".join(down)); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
