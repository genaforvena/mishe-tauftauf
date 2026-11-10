#!/usr/bin/env python3
"""Live window: how the home network and connection are doing, in plain words.

Reads the rows the `network-probe` duty appends to SITE/network/probe.jsonl and, read-only,
a few facts from the router over ssh (never writes there).  Anything not measured, or too old,
is shown as UNKNOWN.  The network mind improves this file.
"""
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
PROBE = SITE / "network" / "probe.jsonl"
SPARK = "▁▂▃▄▅▆▇█"
STALE_S = 300
ROUTER_EVERY_S = 60
SLOW_WEB_MS = 1500
SLOW_DNS_MS = 500
_router = {"at": 0.0, "text": None}
_wifi = {"at": 0.0, "text": None, "weak": False}
WEAK_WIFI_DBM = -75
REPO = Path(__file__).resolve().parent.parent
OWN_FILES = (Path(__file__).resolve(), Path(__file__).resolve().parent / "network_boundary.py")
OWN_GIT_PATHS = ("tools/network_view.py", "tools/network_boundary.py")
WINDOW_STATE = SITE / "network" / "window.json"
STARTED = time.time()


def own_stamp():
    """Modification stamps of this view's own code; a change means the running copy is stale."""
    try:
        return tuple(p.stat().st_mtime_ns for p in OWN_FILES)
    except OSError:
        return None


def code_fingerprint(paths=None):
    """Digest of this view's own code as it is on disk; a running copy whose digest differs is stale."""
    digest = hashlib.sha256()
    for p in (paths or OWN_FILES):
        digest.update(Path(p).read_bytes())
    return digest.hexdigest()


def code_version():
    """(short sha, commit time) of the last commit touching this view's own files; UNKNOWN when git cannot say."""
    try:
        r = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h %ct", "--", *OWN_GIT_PATHS],
                           capture_output=True, text=True, timeout=10)
        sha, ct = nb.decode_git_version(r.stdout)
        dirty = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", "HEAD", "--", *OWN_GIT_PATHS],
                               capture_output=True, timeout=10).returncode != 0
        return (sha + " + uncommitted edits" if dirty else sha), ct
    except (OSError, ValueError, subprocess.SubprocessError):
        return "UNKNOWN", None


VERSION = code_version()
VERSION_AT = time.time()
STAMP = own_stamp()


def report_window_state():
    """Record when this copy of the view started and which code it runs; the window duty reads it."""
    try:
        WINDOW_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = WINDOW_STATE.with_name("window.json.tmp")
        tmp.write_text(json.dumps({"pid": os.getpid(), "started": STARTED, "code": VERSION[0],
                                   "fingerprint": code_fingerprint()}))
        tmp.replace(WINDOW_STATE)
    except OSError:
        pass


def maybe_reexec(stamp):
    """Re-run this file in place when its own code changed on disk and the new copy compiles.

    A copy that does not compile is left running, and the check is retried on the next redraw.
    Returns the stamp to compare against next time."""
    now = own_stamp()
    if now is None or stamp is None or now == stamp:
        return stamp
    try:
        compile(OWN_FILES[0].read_text(), str(OWN_FILES[0]), "exec")
    except (OSError, SyntaxError):
        return stamp
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, str(OWN_FILES[0]), *sys.argv[1:]])


def rows(limit=1440):
    try:
        return nb.decode_rows(PROBE.read_text(), limit)
    except OSError:
        return []


def nb_speed_rows():
    import network_speed
    return network_speed.read_rows(SITE / "network" / network_speed.LOG_NAME)


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


def wifi_facts():
    """(text, weak) for the home Wi-Fi from a read-only router listing; (None, False) when it cannot be read."""
    if time.time() - _wifi["at"] < ROUTER_EVERY_S:
        return _wifi["text"], _wifi["weak"]
    _wifi["at"] = time.time()
    _wifi["text"], _wifi["weak"] = None, False
    cmd = ("for d in $(iwinfo | awk '/ESSID: \"/{print $1}'); do "
           "echo \"BAND $(iwinfo $d info | grep -o '[0-9.]* GHz' | head -1 | cut -d' ' -f1)\"; "
           "iwinfo $d assoclist; done")
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router", cmd],
                           capture_output=True, text=True, timeout=12)
        bands = nb.decode_wifi(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, False
    parts, weakest = [], None
    for band in sorted(bands):
        sig = bands[band]
        parts.append(f"{band} GHz {len(sig)}")
        if sig:
            weakest = min(sig) if weakest is None else min(weakest, min(sig))
    total = sum(len(v) for v in bands.values())
    weak = weakest is not None and weakest < WEAK_WIFI_DBM
    tail = f", weakest {weakest} dBm" if weakest is not None else ""
    _wifi["text"] = f"{total} clients ({'; '.join(parts)}){tail}" + ("  WEAK SIGNAL" if weak else "")
    _wifi["weak"] = weak
    return _wifi["text"], weak


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


def speed_line(speed_rows, now=None):
    """SPEED in plain words: the last good download with its age, or UNKNOWN (never a calm old number)."""
    now = time.time() if now is None else now
    if not speed_rows:
        return "UNKNOWN (no speed reading yet; one 5 MB download a night, 02-05 local)"
    last = speed_rows[-1]
    if not last.get("ok"):
        return f"UNKNOWN (last try {int((now - last['ts']) // 60)} min ago failed: {last.get('error', '?')[:60]})"
    good = [r for r in speed_rows if r.get("ok")]
    best = good[-1]
    hours = (now - best["ts"]) / 3600
    if hours > 36:
        return f"UNKNOWN (last reading is {hours:.0f} h old)"
    return f"DOWN {best['down_mbps']:g} Mbps, measured {hours:.1f} h ago (5 MB download, night only, no upload yet)"


def draw():
    data = rows()
    last = data[-1] if data else None
    a = age(last) if last else None
    out = [f"MISHE NETWORK  {time.strftime('%H:%M:%SZ', time.gmtime())}   derived numbers only; UNKNOWN when not measured", ""]
    sha, committed = VERSION
    committed_txt = f", committed {(time.time() - committed) / 3600:.1f} h ago" if committed else ""
    out.append(f"  view code {sha}{committed_txt}; this copy loaded {time.time() - STARTED:.0f} s ago")
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
    out.append("  SPEED         " + speed_line(nb_speed_rows()))
    wf, _weak = wifi_facts()
    out.append("  WI-FI         " + (wf or "UNKNOWN (router Wi-Fi listing could not be read)") + "   (read-only, clients counted, no devices named)")
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
        report_window_state()
        while True:
            STAMP = maybe_reexec(STAMP)
            if time.time() - VERSION_AT > 60:  # commits and edits show in the header within a minute
                VERSION, VERSION_AT = code_version(), time.time()
            sys.stdout.write("\x1b[H\x1b[2J" + draw() + "\n")
            sys.stdout.flush()
            time.sleep(10)
