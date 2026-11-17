#!/usr/bin/env python3
"""Bounded down/upload speed readings for the network window: rare, off-peak, never a loop.

Runs as a duty. It measures only when the local hour is inside the off-peak window AND the
last attempt is older than MIN_INTERVAL_S, so a duty that fires every few minutes still produces at
most one download and one upload a day, at night. Both are capped in bytes and time. Every attempt
is appended to SITE/network/speed.jsonl (download) and speed-up.jsonl (upload), failures included,
so the window can say "last try failed" instead of showing a calm old number.

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
UP_URL = "https://speed.cloudflare.com/__up"
UP_EXPECT_BYTES = 2_000_000
UP_TIMEOUT_S = 20
OFF_PEAK_HOURS = (2, 5)  # local hours, inclusive start, exclusive end
MIN_INTERVAL_S = 12 * 3600  # between good readings
FAILED_RETRY_S = 30 * 60  # after a failed try, retry inside the window no sooner than this
STALE_AFTER_S = 36 * 3600  # no good reading this old (or never) makes the duty UNKNOWN
CHUNK = 64 * 1024

SITE = Path(os.environ.get("MISHE_TAUFTAUF_HOME") or os.environ.get("MISHE_SEED_HOME") or ".mishe-tauftauf")
LOG_NAME = "speed.jsonl"
UP_LOG_NAME = "speed-up.jsonl"


def clock_hour(now):
    """The local clock hour of an epoch second (the off-peak window is local)."""
    return time.localtime(now).tm_hour


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


def upload_state(rows, now, hour):
    """(True, phrase) when the upload reading can be trusted, else (False, why it is UNKNOWN).

    A log that does not exist yet is honest outside the night window (nothing was due), but once the
    window is open the producer must record an attempt — success or the error — or the reading says
    nothing and the duty must not call that a calm GREEN. A failed attempt or a good reading older
    than STALE_AFTER_S is UNKNOWN for the same reason the window prints UNKNOWN.
    """
    start, end = OFF_PEAK_HOURS
    if not rows:
        if start <= hour < end:
            return False, "no upload attempt recorded in the off-peak window"
        return True, "no upload reading yet"
    last = rows[-1]
    if not last.get("ok"):
        age_min = (now - last["ts"]) / 60 if isinstance(last.get("ts"), (int, float)) else None
        when = f"{age_min:.0f} min ago" if age_min is not None else "at an unreadable time"
        return False, f"last upload {when} failed: {str(last.get('error', '?'))[:80]}"
    ts, mbps = last.get("ts"), last.get("up_mbps")
    if not isinstance(ts, (int, float)) or not isinstance(mbps, (int, float)):
        return False, "last upload row is unreadable"
    hours = (now - ts) / 3600
    if hours > STALE_AFTER_S / 3600:
        return False, f"last upload is {hours:.0f} h old"
    return True, f"upload {mbps:g} Mbps, {hours:.1f} h ago"


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


def measure_up(url=UP_URL, timeout=UP_TIMEOUT_S, opener=urllib.request.urlopen):
    """One bounded upload. Returns a row dict; never raises. No daytime caller reaches this."""
    started = time.time()
    payload = b"x" * UP_EXPECT_BYTES
    try:
        req = urllib.request.Request(url, data=payload, headers={"User-Agent": USER_AGENT}, method="POST")
        with opener(req, timeout=timeout) as resp:
            resp.read()
        seconds = time.time() - started
        if seconds <= 0:
            return _up_row(started, ok=False, error="zero elapsed time", seconds=seconds)
        mbps = UP_EXPECT_BYTES * 8 / seconds / 1_000_000
        return _up_row(started, ok=True, up_mbps=round(mbps, 1), seconds=round(seconds, 2))
    except Exception as exc:  # any failure is a row, never a pass
        return _up_row(started, ok=False, error=type(exc).__name__ + ": " + str(exc)[:120], seconds=time.time() - started)


def _up_row(started, ok, seconds, up_mbps=None, error=None):
    row = {"ts": round(started, 1), "ok": ok, "seconds": round(seconds, 2), "method": "cloudflare-up-2MB-offpeak"}
    if ok:
        row["up_mbps"] = up_mbps
    else:
        row["error"] = error
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=nb.doc_summary(__doc__))
    ap.add_argument("--home", default=str(SITE))
    ap.add_argument("--check", action="store_true", help="print whether a measurement is due, and do nothing else")
    args = nb.parse_argv(ap, argv)
    path = Path(args.home) / "network" / LOG_NAME
    up_path = Path(args.home) / "network" / UP_LOG_NAME
    rows = read_rows(path)
    last = last_attempt(rows)
    good = last_good(rows)
    now = time.time()
    hour = clock_hour(now)
    ok, why = due(now, good["ts"] if good else None, hour)
    if ok and last is not None and not last.get("ok") and now - last["ts"] < FAILED_RETRY_S:
        ok, why = False, "last try failed; retry not before 30 min"
    if args.check:
        print(("due: " if ok else "not due: ") + why)
        return 0
    if ok:
        row = measure()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        print(json.dumps(row, sort_keys=True))
        down_ok = row["ok"]
    else:
        if good is None or now - good["ts"] >= STALE_AFTER_S:
            print("not due: " + why + "; no good reading in the last 36 h, so UNKNOWN")
            down_ok = False
        else:
            print("not due: " + why)
            down_ok = True
    # Upload has its own gate: a failed upload retries inside the same night instead of waiting
    # for the download's 12 h interval. The off-peak check in due() keeps it off the daytime network.
    up_rows = read_rows(up_path)
    up_good = last_good(up_rows)
    up_ok, up_why = due(now, up_good["ts"] if up_good else None, hour)
    up_last = last_attempt(up_rows)
    if up_ok and up_last is not None and not up_last.get("ok") and now - up_last["ts"] < FAILED_RETRY_S:
        up_ok, up_why = False, "last upload failed; retry not before 30 min"
    if up_ok:
        up_row = measure_up()
        up_path.parent.mkdir(parents=True, exist_ok=True)
        with up_path.open("a") as fh:
            fh.write(json.dumps(up_row, sort_keys=True) + "\n")
        print(json.dumps(up_row, sort_keys=True))
    # The verdict is read back after the possible write: a failed attempt is UNKNOWN here too,
    # with the error in plain words, so a night of failing uploads cannot pass as GREEN.
    up_ok_state, up_phrase = upload_state(read_rows(up_path), now, hour)
    print(("upload: " if up_ok_state else "upload UNKNOWN: ") + up_phrase)
    return 0 if (down_ok and up_ok_state) else 3


if __name__ == "__main__":
    sys.exit(main())
