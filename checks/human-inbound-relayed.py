#!/usr/bin/env python3
"""Duty human-inbound-relayed: the human's own words reach a mind.

mesh-voice-rx appends every operator TEXT/VOICE line to ~/.mesh/voice-in.log as
"<UTC ISO time>  TEXT|VOICE  <text>". Mishe never read that file, so answers the
operator gave were never delivered. This piece relays each new human line into the
chat as a DM to the operator role, marked as the human's words, from a byte cursor.

It never calls Telegram and never reads a token. The Telegram getUpdates poller is
owned by mesh-voice-rx; a second poller would wedge it with 409.

Modes:
  (default)   dry run: report what would be relayed and the lag; writes nothing
  --relay     append the new lines as DMs and advance the cursor

Exit: 0 GREEN (no unrelayed human line older than the limit),
      2 RED (a human line older than --limit minutes is not relayed),
      3 UNKNOWN (log unreadable, voice receiver inactive, or cursor unusable).
"""
import argparse
import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+(TEXT|VOICE)\s+(.*)$")
LIMIT_MIN = 10
FIRST_RUN_WINDOW_S = 24 * 3600
DEFAULT_LOG = Path.home() / ".mesh" / "voice-in.log"


def parse_at(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def read_cursor(path):
    try:
        data = json.loads(path.read_text())
        return int(data["offset"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        raise


def write_cursor(path, offset):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"offset": offset}) + "\n")
    os.replace(tmp, path)


def pending(log_bytes, start):
    """Yield (offset_after, at, kind, text) for each well-formed line from start."""
    pos = start
    for raw in log_bytes[start:].splitlines(keepends=True):
        pos += len(raw)
        line = raw.decode("utf-8", "replace").rstrip("\n")
        m = LINE.match(line)
        if m:
            yield pos, parse_at(m.group(1)), m.group(2), m.group(3)


def first_run_offset(log_bytes, now):
    """Byte offset of the first line within the first-run window, so old history is not replayed."""
    pos = 0
    for raw in log_bytes.splitlines(keepends=True):
        m = LINE.match(raw.decode("utf-8", "replace").rstrip("\n"))
        if m and now - parse_at(m.group(1)) <= FIRST_RUN_WINDOW_S:
            return pos
        pos += len(raw)
    return len(log_bytes)


def voice_active():
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", "mesh-voice-rx"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() == "active"


def append_dm(text, mishe_bin):
    cmd = [mishe_bin, "wall", "dm", "--source", "operator", "--to", "operator", "--text", text]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"mishe wall dm failed: {res.stderr.strip() or res.stdout.strip()}")


def main(argv=None, now=None, voice=voice_active, mishe_bin=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", type=Path, required=True, help="Mishe site home (cursor lives here)")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    ap.add_argument("--limit", type=int, default=LIMIT_MIN, help="minutes before an unrelayed line is RED")
    ap.add_argument("--relay", action="store_true", help="write the DMs and advance the cursor")
    a = ap.parse_args(argv)
    now = time.time() if now is None else now
    cursor = a.home / "human-inbound" / "cursor.json"

    try:
        log_bytes = a.log.read_bytes()
    except OSError as exc:
        print(f"human-inbound-relayed: UNKNOWN log unreadable: {exc}", file=sys.stderr)
        return 3
    try:
        offset = read_cursor(cursor)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"human-inbound-relayed: UNKNOWN cursor unusable: {exc}", file=sys.stderr)
        return 3
    if offset is None:
        offset = first_run_offset(log_bytes, now)
        first = True
    else:
        first = False
    if offset > len(log_bytes):
        print("human-inbound-relayed: UNKNOWN cursor beyond log end (log truncated?)", file=sys.stderr)
        return 3

    active = voice()
    if active is None or active is False:
        print("human-inbound-relayed: UNKNOWN mesh-voice-rx is not active; the human channel is deaf", file=sys.stderr)
        return 3

    new = list(pending(log_bytes, offset))
    first_note = " (first run: history older than 24 h not replayed)" if first else ""
    if a.relay:
        mishe_bin = mishe_bin or shutil.which("mishe") or str(a.home / "bin" / "mishe")
        relayed = 0
        try:
            for end, at, kind, text in new:
                stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
                append_dm(f"[human {kind} {stamp}] {text}", mishe_bin)
                write_cursor(cursor, end)
                relayed += 1
        except (RuntimeError, OSError) as exc:
            print(f"human-inbound-relayed: RED relay stopped after {relayed} of {len(new)}: {exc}; "
                  "cursor kept at the last relayed line", file=sys.stderr)
            return 2
        if first and not new:
            write_cursor(cursor, offset)
        print(f"human-inbound-relayed: GREEN {relayed} human line(s) relayed to operator{first_note}")
        return 0
    stale = [at for _, at, _, _ in new if now - at > a.limit * 60]
    detail = f"{len(new)} unrelayed human line(s)"
    if new:
        detail += f", newest {int((now - new[-1][1]) / 60)} min old"
    detail += first_note
    if stale:
        print(f"human-inbound-relayed: RED {detail}; oldest is past {a.limit} min, run with --relay", file=sys.stderr)
        return 2
    print(f"human-inbound-relayed: GREEN {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
