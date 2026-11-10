#!/usr/bin/env python3
"""Bounded download speed reading for the network window: rare, off-peak, never a loop.

Runs as a duty. It measures only when the local hour is inside the off-peak window AND the
last attempt is older than MIN_INTERVAL_S, so a duty that fires hourly still produces at most
one download a day, at night. One fixed-size download (no upload yet), capped in bytes and time.
Every attempt is appended to SITE/network/speed.jsonl, failed ones included, so the window can
say "last try failed" instead of showing a calm old number.

Derived numbers only: no addresses, no device names, no content is kept.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://speed.cloudflare.com/__down?bytes=5000000"
EXPECT_BYTES = 5_000_000
TIMEOUT_S = 25
OFF_PEAK_HOURS = (2, 5)  # local hours, inclusive start, exclusive end
MIN_INTERVAL_S = 12 * 3600
CHUNK = 64 * 1024

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
LOG_NAME = "speed.jsonl"


def due(now, last_ts, local_hour):
    """(True, reason) when a measurement should run now, else (False, why not)."""
    start, end = OFF_PEAK_HOURS
    if not (start <= local_hour < end):
        return False, f"outside off-peak hours ({start:02d}-{end:02d} local)"
    if last_ts is not None and now - last_ts < MIN_INTERVAL_S:
        return False, "measured recently"
    return True, "due"


def last_attempt(rows):
    """Most recent row (ok or failed), or None."""
    return rows[-1] if rows else None


def read_rows(path):
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def measure(url=URL, timeout=TIMEOUT_S, opener=urllib.request.urlopen):
    """One bounded download. Returns a row dict; never raises."""
    started = time.time()
    got = 0
    try:
        with opener(url, timeout=timeout) as resp:
            while got < EXPECT_BYTES:
                chunk = resp.read(min(CHUNK, EXPECT_BYTES - got))
                if not chunk:
                    break
                got += len(chunk)
        seconds = time.time() - started
        if got < EXPECT_BYTES:
            return _row(started, ok=False, error=f"short read {got} of {EXPECT_BYTES} bytes", seconds=seconds, got=got)
        if seconds <= 0:
            return _row(started, ok=False, error="zero elapsed time", seconds=seconds, got=got)
        mbps = got * 8 / seconds / 1_000_000
        return _row(started, ok=True, down_mbps=round(mbps, 1), seconds=round(seconds, 2), got=got)
    except Exception as exc:  # any failure is a row, never a pass
        return _row(started, ok=False, error=type(exc).__name__ + ": " + str(exc)[:120], seconds=time.time() - started, got=got)


def _row(started, ok, got, seconds, down_mbps=None, error=None):
    row = {"ts": round(started, 1), "ok": ok, "bytes": got, "seconds": round(seconds, 2), "method": "cloudflare-down-5MB-offpeak"}
    if ok:
        row["down_mbps"] = down_mbps
    else:
        row["error"] = error
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", default=str(SITE))
    ap.add_argument("--check", action="store_true", help="print whether a measurement is due, and do nothing else")
    args = ap.parse_args(argv)
    path = Path(args.home) / "network" / LOG_NAME
    rows = read_rows(path)
    last = last_attempt(rows)
    now = time.time()
    ok, why = due(now, last["ts"] if last else None, time.localtime(now).tm_hour)
    if args.check or not ok:
        print(("due: " if ok else "not due: ") + why)
        return 0
    row = measure()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, sort_keys=True))
    return 0 if row["ok"] else 3  # 3 = UNKNOWN in the duty contract, not RED


if __name__ == "__main__":
    sys.exit(main())
