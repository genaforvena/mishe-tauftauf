#!/usr/bin/env python3
"""Read-only collector of the router's sing-box log lines, so probe timing can be joined over hours.

The router keeps only about 18 minutes of logread, so a probe row from an hour ago has no router-side
coverage unless something pulls the lines while they still exist. One run does one `ssh router` read of
the sing-box lines, converts the router's MSK stamps to UTC, and appends only lines not seen before to
SITE/analysis/router-singbox-stream.log. Dedupe key is the whole raw line (sing-box numbers each
connection, so a raw line is unique). Nothing on the router is changed.

Every run writes SITE/analysis/router-singbox-stream.status.json with the time of its last attempt and
its last success, failures included, so a dead collector reads as stale, never as a quiet router.

Run from a timer or by hand, e.g. `python3 tools/network_singbox_stream.py`. A run is cheap: one ssh
call with a 15 s timeout, under `nice`.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_boundary as nb  # noqa: E402  (stderr_summary helper shared with the other probes)

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
STREAM_NAME = "analysis/router-singbox-stream.log"
STATUS_NAME = "analysis/router-singbox-stream.status.json"
TIMEOUT_S = 15
MSK = dt.timezone(dt.timedelta(hours=3))  # logread prints the router's local zone (MSK, UTC+3)
# logread stamp: "Fri Nov 27 16:22:10 2026 " then the line body.
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STAMP = re.compile(r"^\w{3} (\w{3}) +(\d+) (\d\d:\d\d:\d\d) (\d{4}) ")
REMOTE = "nice -n 10 logread 2>/dev/null | grep 'sing-box' | tail -n 3000"


def to_utc_line(raw):
    """'<UTC iso> <raw>' for a logread line, or None when the line has no readable stamp."""
    m = STAMP.match(raw)
    if not m:
        return None
    t = dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)} {m.group(4)}", "%b %d %H:%M:%S %Y")
    utc = t.replace(tzinfo=MSK).astimezone(dt.timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ") + " " + raw


def seen_keys(path):
    """Raw lines already stored (the part after the UTC stamp), so a re-read adds nothing twice."""
    if not path.exists():
        return set()
    keys = set()
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            keys.add(line.rstrip("\n").split(" ", 1)[1] if " " in line else line)
    return keys


def write_status(home, now, ok, error=None, added=0):
    status = {"last_attempt": now, "ok": ok}
    prev = {}
    p = home / STATUS_NAME
    if p.exists():
        try:
            prev = json.loads(p.read_text())
        except (ValueError, OSError):
            prev = {}
    if ok:
        status["last_success"] = now
        status["added_last_run"] = added
    else:
        status["last_success"] = prev.get("last_success")
        status["error"] = error
    p.write_text(json.dumps(status, indent=1) + "\n")
    return status


def collect(home, now=None):
    """Pull once; append new lines; return the status dict. Never raises for a failed read."""
    now = now if now is not None else time.time()
    stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (home / "analysis").mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router", REMOTE],
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return write_status(home, stamp, False, error="ssh timed out")
    if r.returncode != 0:
        return write_status(home, stamp, False, error=f"ssh exit {r.returncode}: " + nb.stderr_summary(r.stderr))
    lines = [ANSI.sub("", ln.rstrip("\r\n")) for ln in r.stdout.splitlines() if ln.strip()]
    if lines and not any(STAMP.match(ln) for ln in lines):
        return write_status(home, stamp, False, error="output has no logread stamps")
    stream = home / STREAM_NAME
    seen = seen_keys(stream)
    fresh = []
    for ln in lines:
        key = ln
        if key in seen:
            continue
        seen.add(key)
        out = to_utc_line(ln)
        if out is not None:
            fresh.append(out)
    if fresh:
        with stream.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(fresh) + "\n")
    return write_status(home, stamp, True, added=len(fresh))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--home", default=str(SITE))
    args = ap.parse_args(argv)
    status = collect(Path(args.home))
    print(json.dumps(status))
    # 0 = read and stored (or nothing new); 3 = the router read failed, so the duty reports UNKNOWN.
    return 0 if status["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
