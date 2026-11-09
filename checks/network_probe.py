#!/usr/bin/env python3
"""One small network probe: append a derived row to SITE/network/probe.jsonl.

What it measures, and why it is not ping to public addresses:
- Latency and loss to the default gateway (the router, on the LAN): real, ICMP is fine there.
- Internet reachability: certificate-verified HTTPS to two stable sites.  The home router answers
  ICMP echo and accepts TCP for ANY address (even 203.0.113.1, a non-routable test address, answers
  in ~1 ms), so ping to 1.1.1.1 or 8.8.8.8 says nothing about the internet.  A secure fetch that
  returns the expected status only succeeds when the real site is reached.
- DNS lookup time through the home resolver.
No content is kept.  A probe that cannot run exits 3 (UNKNOWN); a probe that runs and finds the
internet unreachable exits 2 (RED).  It never reports a quiet network from a dead probe.
"""
import argparse, json, re, socket, subprocess, sys, time
from pathlib import Path

# name -> (url, the HTTP status that proves the real site answered)
INTERNET = {
    "gstatic": ("https://www.gstatic.com/generate_204", 204),
    "cloudflare": ("https://www.cloudflare.com/cdn-cgi/trace", 200),
}
DNS_NAME = "www.wikipedia.org"
CURL_FMT = "%{http_code} %{time_connect} %{time_appconnect} %{time_total}"


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


def parse_fetch(stdout, returncode, expected_status):
    """Decode one curl timing line.  ok only when curl succeeded and the expected status came back."""
    parts = stdout.split()
    if returncode != 0 or len(parts) != 4 or parts[0] != str(expected_status):
        return {"ok": False, "curl_exit": returncode}
    _, conn, tls, total = parts
    return {"ok": True, "connect_ms": round(float(conn) * 1000, 1),
            "tls_ms": round(float(tls) * 1000, 1), "total_ms": round(float(total) * 1000, 1)}


def fetch(url, expected_status):
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-m", "10", "-w", CURL_FMT, url],
                       capture_output=True, text=True, timeout=15)
    return parse_fetch(r.stdout, r.returncode, expected_status)


def dns_ms():
    t = time.monotonic()
    try:
        socket.getaddrinfo(DNS_NAME, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    return round((time.monotonic() - t) * 1000, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", required=True)
    a = ap.parse_args()
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        gw = gateway()
        row["gateway_ip"] = gw
        row["gateway"] = ping(gw)
        row["internet"] = {name: fetch(url, status) for name, (url, status) in INTERNET.items()}
        row["dns_ms"] = dns_ms()
    except (OSError, subprocess.SubprocessError):
        return 3
    out = Path(a.home) / "network" / "probe.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    gw_down = row["gateway"] is None or row["gateway"]["loss_pct"] >= 50
    internet_down = not any(t["ok"] for t in row["internet"].values())
    if gw_down or internet_down:
        print("network down: " + ("router/LAN not answering" if gw_down else "") +
              (" internet unreachable (no secure site answered)" if internet_down else ""))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
