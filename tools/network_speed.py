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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import network_boundary as nb

URL = "https://speed.cloudflare.com/__down?bytes=5000000"
# Cloudflare answers the default "Python-urllib" User-Agent with HTTP 403 (seen 02:01Z, 11 November);
# a named client agent gets the file. Name it, do not impersonate a browser.
USER_AGENT = "mishe-network-speed/1.0"
EXPECT_BYTES = 5_000_000
TIMEOUT_S = 25
OFF_PEAK_HOURS = (2, 5)  # local hours, inclusive start, exclusive end
MIN_INTERVAL_S = 12 * 3600  # between good readings
FAILED_RETRY_S = 30 * 60  # after a failed try, retry inside the window no sooner than this
STALE_AFTER_S = 36 * 3600  # no good reading this old (or never) makes the duty UNKNOWN
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


def last_good(rows):
    """Most recent successful row, or None."""
    good = [r for r in rows if r.get("ok") and isinstance(r.get("ts"), (int, float))]
    return good[-1] if good else None


def read_rows(path):
    """The decoded rows of the speed log, oldest first. A missing log is no rows."""
    try:
        text = Path(path).read_text()
    except OSError:
        return []
    return nb.decode_json_lines(text)


def measure(url=URL, timeout=TIMEOUT_S, opener=urllib.request.urlopen):
    """One bounded download. Returns a row dict; never raises."""
    started = time.time()
    got = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with opener(req, timeout=timeout) as resp:
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
    ap = argparse.ArgumentParser(description=nb.doc_summary(__doc__))
    ap.add_argument("--home", default=str(SITE))
    ap.add_argument("--check", action="store_true", help="print whether a measurement is due, and do nothing else")
    args = nb.parse_argv(ap, argv)
    path = Path(args.home) / "network" / LOG_NAME
    rows = read_rows(path)
    last = last_attempt(rows)
    good = last_good(rows)
    now = time.time()
    ok, why = due(now, good["ts"] if good else None, time.localtime(now).tm_hour)
    if ok and last is not None and not last.get("ok") and now - last["ts"] < FAILED_RETRY_S:
        ok, why = False, "last try failed; retry not before 30 min"
    if args.check:
        print(("due: " if ok else "not due: ") + why)
        return 0
    if not ok:
        # Not measuring now is fine only while a good reading is recent; otherwise the signal must say UNKNOWN.
        if good is not None and now - good["ts"] < STALE_AFTER_S:
            print("not due: " + why)
            return 0
        print("not due: " + why + "; no good reading in the last 36 h, so UNKNOWN")
        return 3  # 3 = UNKNOWN in the duty contract, not RED
    row = measure()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps(row, sort_keys=True))
    return 0 if row["ok"] else 3  # 3 = UNKNOWN in the duty contract, not RED


if __name__ == "__main__":
    sys.exit(main())
